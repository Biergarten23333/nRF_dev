#!/usr/bin/env python3
"""Guarded single-tag Path-R controls for the relay8 staged campaign."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_control import relay_command_patient
from batch_g_overnight import NODES, TAG_NUMBER
from batch_g_overnight_core import composed_idle_cfg
from capacity_ramp import RecordingAssembler, b306_command, relay_command
from coldstart_fusion_control import decode_guard
from fusion_session import SessionError, parse_fields, resolve_fusion_port
from pre_ramp_hardening import request_list


MASTER_MARKER = "dk-fusion-imu-relay-v29"
B306_MARKER = "b306-imu-relay-v32"
RELAY8_MARKER = "tag-fusion-link-relay8"
RELAY8_IMGSTAT = (
    "69f8b6a1e4718d84156c8dbceb630fa578bf6d3d78ccec82da9cac5b6859bb26"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="milliseconds"
    )


def wait_master_status(channel: ThreadedLineChannel) -> str:
    channel.send("MASTER STATUS")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line and line.startswith("FUSION_MASTER_STATUS "):
            if parse_fields(line).get("marker") != MASTER_MARKER:
                raise SessionError(f"Fusion Master marker mismatch: {line}")
            return line
    raise SessionError("Fusion Master status timed out")


def require_fleet(
    channel: ThreadedLineChannel,
    node: str,
    allow_offline: tuple[str, ...],
) -> dict[str, object]:
    if node in allow_offline:
        raise SessionError(f"target {node} is explicitly allowed offline")
    expected = set(NODES) - set(allow_offline)
    listing = request_list(channel, RecordingAssembler(), {}, tuple(NODES))
    aggregate = listing["aggregate"]
    peer = listing["peers"].get(node, {})
    if (
        aggregate.get("count") != str(len(expected))
        or aggregate.get("ready") != str(len(expected))
        or aggregate.get("spacing") != "ON"
        or aggregate.get("spacing_us") != "5000"
        or set(listing["peers"]) != expected
        or peer.get("connected") != "1"
        or peer.get("subscribed") != "1"
        or peer.get("link_contract") != "PASS"
    ):
        raise SessionError(
            f"fleet gate failed expected={sorted(expected)} "
            f"allow_offline={sorted(allow_offline)}: {listing}"
        )
    return listing


def query(
    channel: ThreadedLineChannel,
    node: str,
    reply_timeout_s: float | None = None,
) -> dict[str, object]:
    ping = b306_command(channel, node, "PING", "PONG ")
    if f"name={node}" not in ping["text"] or f"fw={B306_MARKER}" not in ping["text"]:
        raise SessionError(f"B306 identity mismatch: {ping['text']}")
    if reply_timeout_s is None:
        reply_timeout_s = 100.0 if node == "BSFB165" else 15.0
    version = relay_command_patient(
        channel, node, "VERSION", "VERSION ", attempts=1,
        reply_timeout_s=reply_timeout_s,
    )
    imgstat = relay_command_patient(
        channel, node, "IMGSTAT", "IMGSTAT ", attempts=1,
        reply_timeout_s=reply_timeout_s,
    )
    return {"ping": ping, "version": version, "imgstat": imgstat}


def cfg110(channel: ThreadedLineChannel, node: str) -> dict[str, object]:
    tag = TAG_NUMBER[node]
    command = (
        f"CFG TAG={tag} SLOT=10 COUNT=11 PERIOD=10 ACTIVE=9 EPOCH=5000 "
        "BEACON_SYNC=1 BEACON_WIN_N=1 DW_ANCHOR=0 RUN=1 PMODE=0"
    )
    reply = relay_command_patient(
        channel, node, command, "CFG_OK ", attempts=1, reply_timeout_s=15.0
    )
    text = reply["reply"]["text"]
    required = (
        f"TAG={tag}", "SLOT=10/11", "PERIOD=10", "ACTIVE=9",
        "BEACON_SYNC=1", "BEACON_WIN_N=1", "DW_ANCHOR=0",
        "LIVE=1", "RUN=1", "STATE=RUNNING",
    )
    if any(token not in text for token in required):
        raise SessionError(f"incomplete relay8 slot-10 CFG echo: {text}")
    return {"command": command, "reply": reply}


def idle(channel: ThreadedLineChannel, node: str) -> dict[str, object]:
    tag = TAG_NUMBER[node]
    command = composed_idle_cfg(tag, min(tag, 10), 11)
    reply = relay_command_patient(
        channel, node, command, "CFG_OK ", attempts=1, reply_timeout_s=15.0
    )
    text = reply["reply"]["text"]
    required = (
        f"TAG={tag}", "SLOT=0/1", "BEACON_SYNC=0", "LIVE=1",
        "RUN=0", "STATE=ARMED",
    )
    if any(token not in text for token in required):
        raise SessionError(f"incomplete composed-idle CFG echo: {text}")
    return {"command": command, "reply": reply}


def beacon_status(channel: ThreadedLineChannel, node: str) -> dict[str, object]:
    reply = relay_command_patient(
        channel, node, "BEACON_STATUS", "BEACON ", attempts=1,
        reply_timeout_s=15.0,
    )
    text = reply["reply"]["text"]
    required = (
        "sync=", "lock=", "rx=", "promoted=", "mismatch=", "miss=",
        "gen=", "counter=",
    )
    if any(token not in text for token in required):
        raise SessionError(f"incomplete relay8 BEACON_STATUS: {text}")
    return {"reply": reply, "fields": parse_fields(text)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("query", "verify-relay8", "cfg110", "idle", "beacon-status")
    )
    parser.add_argument("--node", required=True, choices=tuple(NODES))
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument(
        "--reply-timeout-s",
        type=float,
        help="Patient receive window for each single-shot VERSION/IMGSTAT query.",
    )
    parser.add_argument(
        "--allow-offline", action="append", default=[], choices=tuple(NODES),
        help="Explicit node permitted to be absent (repeatable).",
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {
        "status": "IN_PROGRESS", "started": utc_now(), "action": args.action,
        "node": args.node,
    }
    channel: ThreadedLineChannel | None = None
    with (args.out_dir / "fusion_cdc.log").open(
        "x", encoding="utf-8", buffering=1
    ) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(args.fusion_port), log, "FUSION",
                decoded_queue_records=65536, backlog_red_records=8192,
                raw_backlog_red_bytes=8192, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["port"] = channel.port
            result["decode_before_send"] = decode_guard(channel, 15.0)
            result["master_status"] = wait_master_status(channel)
            result["allow_offline"] = sorted(set(args.allow_offline))
            result["fleet"] = require_fleet(
                channel, args.node, tuple(result["allow_offline"])
            )
            if args.action in ("query", "verify-relay8"):
                result["query"] = query(
                    channel, args.node, args.reply_timeout_s
                )
                if args.action == "verify-relay8":
                    version_text = result["query"]["version"]["reply"]["text"]
                    imgstat_text = result["query"]["imgstat"]["reply"]["text"]
                    if f"fw={RELAY8_MARKER}" not in version_text:
                        raise SessionError(f"relay8 marker mismatch: {version_text}")
                    if RELAY8_IMGSTAT not in imgstat_text or "confirmed=1" not in imgstat_text:
                        raise SessionError(f"relay8 IMGSTAT mismatch: {imgstat_text}")
            elif args.action == "cfg110":
                result["cfg110"] = cfg110(channel, args.node)
            elif args.action == "beacon-status":
                result["beacon_status"] = beacon_status(channel, args.node)
            else:
                result["idle"] = idle(channel, args.node)
            result["host_drain"] = channel.health_snapshot()
            if result["host_drain"]["red_markers"]:
                raise SessionError(f"host drain RED: {result['host_drain']}")
            result["status"] = "PASS"
            return 0
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None:
                result.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            result["ended"] = utc_now()
            (args.out_dir / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, SessionError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
