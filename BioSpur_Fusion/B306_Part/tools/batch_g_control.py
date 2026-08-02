#!/usr/bin/env python3
"""Guarded Batch-G Path-R IDLE and TDMA configuration actions."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import RecordingAssembler, collect, relay_command
from coldstart_fusion_control import decode_guard
from fusion_session import (
    FusionController,
    LineChannel,
    SessionError,
    parse_fields,
    parse_reply,
    resolve_fusion_port,
)
from pre_ramp_hardening import request_list


EXPECTED_MARKER = "dk-fusion-imu-relay-v27"
TAG_NUMBER = {
    "BSF3C79": 1,
    "BSFC2CC": 2,
    "BSF44AD": 3,
    "BSF6C53": 4,
    "BSF8BC4": 5,
    "BSF1120": 6,
    "BSF31CC": 7,
    "BSFAA61": 8,
    "BSFB165": 9,
    "BSFEC35": 10,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="milliseconds"
    )


def names(text: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in text.split(",") if item.strip())


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def wait_prefix(channel: LineChannel, prefix: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is not None and line.startswith(prefix):
            return line
    raise SessionError(f"timeout waiting for {prefix!r}")


def relay_command_patient(
    channel: LineChannel,
    node: str,
    text: str,
    expected_prefix: str,
    attempts: int = 3,
    reply_timeout_s: float = 8.0,
) -> dict[str, object]:
    """Wait through an early tag TIMEOUT for a late correlated CFG_OK."""
    errors: list[object] = []
    for attempt in range(1, attempts + 1):
        controller = FusionController(
            channel, node, timeout_s=8.0, max_attempts=3
        )
        queued = controller.command(
            f"TAG RAW {text}",
            lambda reply: reply.startswith("RELAY_QUEUED"),
            source="B306",
            allow_resend_after_tx=False,
        )
        observed: list[str] = []
        deadline = time.monotonic() + reply_timeout_s
        while time.monotonic() < deadline:
            line = channel.read(deadline)
            if line is None:
                continue
            reply = parse_reply(line)
            if (
                reply is None
                or reply.source != "TAG"
                or reply.correlation != queued.correlation
            ):
                continue
            observed.append(reply.text)
            if reply.text.startswith(expected_prefix):
                return {
                    "attempt": attempt,
                    "queued": queued.__dict__,
                    "reply": reply.__dict__,
                    "observed_before_accept": observed,
                    "reply_timeout_s": reply_timeout_s,
                }
        errors.append(
            {
                "attempt": attempt,
                "correlation": queued.correlation,
                "observed": observed,
            }
        )
    raise SessionError(
        f"{node} TAG RAW {text} failed with patient replies: {errors}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("idle", "cfg100", "cfg110", "rollback_idle_cfg"),
    )
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--peers", required=True, help="complete connected set")
    parser.add_argument("--targets", required=True)
    parser.add_argument("--fusion-port")
    parser.add_argument(
        "--master-marker",
        default=EXPECTED_MARKER,
        help="expected Fusion Master firmware marker",
    )
    parser.add_argument(
        "--accept-cfg-by-behavior",
        action="store_true",
        help=(
            "send each CFG once and retain a missing CFG_OK as a finding; "
            "the bounded UWB witness remains mandatory"
        ),
    )
    parser.add_argument(
        "--accept-idle-by-behavior",
        action="store_true",
        help=(
            "send each MODE IDLE once and retain a missing MODE_OK as a "
            "finding; the bounded zero-UWB witness remains mandatory"
        ),
    )
    args = parser.parse_args()

    peers = names(args.peers)
    targets = names(args.targets)
    if not peers or len(set(peers)) != len(peers):
        raise SessionError("--peers must be a distinct non-empty set")
    if not targets or not set(targets).issubset(peers):
        raise SessionError("--targets must be a non-empty subset of --peers")
    if args.action != "idle" and not set(targets).issubset(TAG_NUMBER):
        raise SessionError("no registered tag number for one or more targets")
    if args.action == "cfg100" and any(TAG_NUMBER[name] > 5 for name in targets):
        raise SessionError("cfg100 is reserved for original tags 1..5")
    args.out_dir.mkdir(parents=True, exist_ok=False)

    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": utc_now(),
        "action": args.action,
        "peers": peers,
        "targets": targets,
    }
    write_json(args.out_dir / "summary.json", result)
    assembler = RecordingAssembler()
    counters: dict[str, int] = {}
    channel: LineChannel | None = None

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
            master_line = wait_prefix(
                channel, "FUSION_MASTER_STATUS ", 5.0
            )
            if parse_fields(master_line).get("marker") != args.master_marker:
                raise SessionError(f"marker mismatch: {master_line}")
            result["master_status"] = master_line

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

            replies: dict[str, object] = {}
            for node in targets:
                if args.action == "idle":
                    text = "MODE IDLE"
                    expected = "MODE_OK MODE=IDLE"
                elif args.action == "rollback_idle_cfg":
                    tag = TAG_NUMBER[node]
                    text = (
                        f"CFG TAG={tag} SLOT={tag} COUNT=11 "
                        "PERIOD=10 ACTIVE=9 EPOCH=5000 "
                        "BEACON_SYNC=0 BEACON_WIN_N=1 DW_ANCHOR=0 "
                        "RUN=0 PMODE=3"
                    )
                    expected = "CFG_OK "
                else:
                    tag = TAG_NUMBER[node]
                    count = 10 if args.action == "cfg100" else 11
                    text = (
                        f"CFG TAG={tag} SLOT={tag} COUNT={count} "
                        "PERIOD=10 ACTIVE=9 EPOCH=5000 "
                        "BEACON_SYNC=1 BEACON_WIN_N=1 DW_ANCHOR=0"
                    )
                    expected = "CFG_OK "
                missing_ack = None
                try:
                    if args.action == "idle":
                        relayed = relay_command(
                            channel,
                            node,
                            text,
                            expected,
                            attempts=(
                                1 if args.accept_idle_by_behavior else 5
                            ),
                        )
                    else:
                        relayed = relay_command_patient(
                            channel,
                            node,
                            text,
                            expected,
                            attempts=(
                                1 if args.accept_cfg_by_behavior else 3
                            ),
                        )
                except SessionError as exc:
                    accept_missing = (
                        args.action != "idle"
                        and args.accept_cfg_by_behavior
                    ) or (
                        args.action == "idle"
                        and args.accept_idle_by_behavior
                    )
                    if not accept_missing:
                        raise
                    relayed = None
                    missing_ack = str(exc)
                ack = (
                    relayed["reply"]["text"] if relayed is not None else None
                )
                if args.action != "idle" and ack is not None:
                    if args.action == "rollback_idle_cfg":
                        # PMODE=3 invokes the tag's IDLE defaults after
                        # parsing, so the reply must describe that normalized
                        # runtime state rather than the input slot/count.
                        required = (
                            f"TAG={TAG_NUMBER[node]}",
                            "SLOT=0/1",
                            "PERIOD=25",
                            "ACTIVE=25",
                            "GEN=0",
                            "BEACON_SYNC=0",
                            "BEACON_WIN_N=1",
                            "DW_ANCHOR=0",
                            "LIVE=1",
                            "RUN=0",
                            "STATE=ARMED",
                        )
                    else:
                        required = (
                            f"TAG={TAG_NUMBER[node]}",
                            f"SLOT={TAG_NUMBER[node]}/"
                            f"{10 if args.action == 'cfg100' else 11}",
                            "BEACON_SYNC=1",
                            "BEACON_WIN_N=1",
                            "DW_ANCHOR=0",
                            "LIVE=1",
                            "RUN=1",
                        )
                    if any(token not in ack for token in required):
                        raise SessionError(
                            f"{node} incomplete CFG echo: {ack}"
                        )
                replies[node] = {
                    "command": text,
                    "relay": relayed,
                    "missing_ack": missing_ack,
                }
            result["replies"] = replies

            # Behavioral confirmation: IDLE targets must cease UWB; running
            # CFG targets must each emit at least 8 Hz.  The relay7
            # rollback-IDLE transition has measured completion/ACK latency
            # up to about 76 s, so its one-shot witness deliberately waits
            # 90 s rather than misclassifying an in-flight transition.
            uwb: Counter[str] = Counter()
            witness_duration_s = (
                90.0 if args.action == "rollback_idle_cfg" else 10.0
            )
            witness_started = time.monotonic()
            witness_deadline = witness_started + witness_duration_s
            while time.monotonic() < witness_deadline:
                line = channel.read(
                    min(witness_deadline, time.monotonic() + 0.5)
                )
                if line and line.startswith("FUSION_UWB "):
                    name = parse_fields(line).get("name")
                    if name in targets:
                        uwb[name] += 1
            elapsed = time.monotonic() - witness_started
            rates = {name: uwb[name] / elapsed for name in targets}
            if args.action in ("idle", "rollback_idle_cfg"):
                # A record already queued in B306/DK before MODE_OK may be
                # delivered after the ACK. Accept at most one such drain
                # record per target; sustained ranging is unambiguously >8 Hz.
                witness_pass = all(uwb[name] <= 1 for name in targets)
            else:
                witness_pass = all(rates[name] >= 8.0 for name in targets)
            result["witness"] = {
                "duration_s": elapsed,
                "uwb_counts": dict(uwb),
                "uwb_rates_hz": rates,
                "pass": witness_pass,
            }
            if not witness_pass:
                raise SessionError(
                    f"{args.action} behavioral witness failed: {rates}"
                )
            result["status"] = "PASS"
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None:
                channel.close()
            result["ended"] = utc_now()
            write_json(args.out_dir / "summary.final.json", result)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
