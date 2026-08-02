#!/usr/bin/env python3
"""Guarded Batch-G BEACON_STATUS snapshot for an exact Fusion peer set."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import RecordingAssembler, collect, relay_command
from coldstart_fusion_control import decode_guard
from fusion_session import (
    LineChannel,
    SessionError,
    parse_fields,
    resolve_fusion_port,
)
from pre_ramp_hardening import request_list


EXPECTED_MARKER = "dk-fusion-imu-relay-v27"


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="milliseconds"
    )


def names(text: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in text.split(",") if item.strip())


def wait_prefix(channel: LineChannel, prefix: str, timeout_s: float) -> str:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is not None and line.startswith(prefix):
            return line
    raise SessionError(f"timeout waiting for {prefix!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--peers", required=True)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()

    peers = names(args.peers)
    if not peers or len(set(peers)) != len(peers):
        raise SessionError("--peers must be a distinct non-empty set")
    args.out_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": utc_now(),
        "peers": peers,
    }
    channel: LineChannel | None = None
    assembler = RecordingAssembler()
    counters: dict[str, int] = {}

    with (args.out_dir / "fusion_cdc.log").open(
        "x", encoding="utf-8", buffering=1
    ) as raw:
        try:
            channel = LineChannel(
                resolve_fusion_port(args.fusion_port), raw, "FUSION"
            )
            result["port"] = channel.port
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_before_send"] = decode_guard(channel, 15.0)

            channel.send("MASTER STATUS")
            master = wait_prefix(channel, "FUSION_MASTER_STATUS ", 5.0)
            if parse_fields(master).get("marker") != EXPECTED_MARKER:
                raise SessionError(f"marker mismatch: {master}")
            result["master_status"] = master

            collect(channel, assembler, 1.0)
            listing = request_list(channel, assembler, counters, peers)
            aggregate = listing["aggregate"]
            if (
                aggregate.get("count") != str(len(peers))
                or aggregate.get("ready") != str(len(peers))
                or set(listing["peers"]) != set(peers)
                or aggregate.get("spacing") != "ON"
                or aggregate.get("spacing_us") != "5000"
            ):
                raise SessionError(f"connected/spacing gate failed: {listing}")
            result["list"] = listing

            statuses: dict[str, object] = {
                peer: {"samples": []} for peer in peers
            }
            required = (
                "sync=1",
                "lock=1",
                "promoted=0",
                "gen=1",
                "dw=0",
                "win=1",
            )
            for sample_index in range(2):
                if sample_index:
                    time.sleep(1.0)
                for peer in peers:
                    reply = relay_command(
                        channel,
                        peer,
                        "BEACON_STATUS",
                        "BEACON ",
                        attempts=3,
                    )
                    text = reply["reply"]["text"]
                    missing = [
                        token for token in required if token not in text
                    ]
                    fields = parse_fields(text)
                    statuses[peer]["samples"].append(
                        {
                            "round_trip": reply,
                            "fields": fields,
                            "missing": missing,
                        }
                    )
                    if missing:
                        raise SessionError(
                            f"{peer} BEACON_STATUS missing {missing}: {text}"
                        )
            for peer, status in statuses.items():
                samples = status["samples"]
                before = int(samples[0]["fields"]["mismatch"])
                after = int(samples[1]["fields"]["mismatch"])
                status["mismatch_before"] = before
                status["mismatch_after"] = after
                status["mismatch_delta"] = after - before
                status["required"] = required
                if after != before:
                    raise SessionError(
                        f"{peer} mismatch still advancing: {before}->{after}"
                    )
            result["beacon_status"] = statuses
            result["status"] = "PASS"
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None:
                channel.close()
            result["ended"] = utc_now()
            (args.out_dir / "summary.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
