#!/usr/bin/env python3
"""Bounded cold-start control actions over the native Fusion Master CDC."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import (
    BSFS as OLD_BSFS,
    RecordingAssembler,
    b306_command,
    collect,
    relay_command,
)
from fusion_session import LineChannel, SessionError, resolve_fusion_port
from pre_ramp_hardening import request_list
from validate_layer2_v31 import wait_spacing


EXPECTED_B306 = "b306-imu-relay-v31"
EXPECTED_TAG = "tag-fusion-link-relay7"
EXPECTED_IMGSTAT = (
    "f60652e54842719e11bd373aee8039c1f6fdd1ed0e6f303a0c49cac444073c82"
)
TAG_SLOT = {
    "BSF3C79": 1,
    "BSFC2CC": 2,
    "BSF44AD": 3,
    "BSF6C53": 4,
    "BSF8BC4": 5,
}
TAG_ID = dict(TAG_SLOT)
NEW_TAG_SLOT = {
    "BSF1120": 1,
    "BSF31CC": 2,
    "BSFAA61": 3,
    "BSFB165": 4,
    "BSFEC35": 5,
}
NEW_TAG_ID = {
    "BSF1120": 6,
    "BSF31CC": 7,
    "BSFAA61": 8,
    "BSFB165": 9,
    "BSFEC35": 10,
}
PROFILES = {
    "old5": (tuple(OLD_BSFS), TAG_SLOT, TAG_ID),
    "new5": (tuple(NEW_TAG_SLOT), NEW_TAG_SLOT, NEW_TAG_ID),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="milliseconds"
    )


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RuntimeError(f"refusing existing output: {path}")
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def decode_guard(channel: LineChannel, timeout_s: float = 10.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is None:
            continue
        if line.startswith("FUSION_"):
            return line
    raise SessionError("Fusion CDC decode-before-send guard timed out")


def list_gate(channel: LineChannel, bsfs: tuple[str, ...]) -> dict:
    assembler = RecordingAssembler()
    counters: dict[str, int] = {}
    collect(channel, assembler, 1.0)
    listing = request_list(channel, assembler, counters, bsfs)
    aggregate = listing["aggregate"]
    peers = listing["peers"]
    errors: list[str] = []
    if aggregate.get("count") != "5" or aggregate.get("ready") != "5":
        errors.append(f"aggregate={aggregate}")
    if set(peers) != set(bsfs):
        errors.append(f"peer_set={sorted(peers)}")
    expected_link = {
        "interval_units": "40",
        "latency": "0",
        "timeout_units": "400",
        "phy_tx": "2",
        "phy_rx": "2",
    }
    for node in bsfs:
        peer = peers.get(node, {})
        mismatches = {
            key: {"actual": peer.get(key), "expected": value}
            for key, value in expected_link.items()
            if peer.get(key) != value
        }
        if mismatches:
            errors.append(f"{node}={mismatches}")
    if errors:
        raise SessionError("strict five-link gate failed: " + "; ".join(errors))
    return listing


def phase_idle(
    channel: LineChannel, bsfs: tuple[str, ...]
) -> dict[str, object]:
    listing = list_gate(channel, bsfs)
    replies = {
        node: relay_command(
            channel, node, "MODE IDLE", "MODE_OK MODE=IDLE", attempts=3
        )
        for node in bsfs
    }
    return {"list": listing, "mode_idle": replies}


def _ensure_spacing(
    channel: LineChannel, bsfs: tuple[str, ...]
) -> dict[str, object]:
    listing = list_gate(channel, bsfs)
    aggregate = listing["aggregate"]
    transition: dict[str, object] | None = None
    if (
        aggregate.get("spacing") != "ON"
        or aggregate.get("spacing_us") != "10000"
    ):
        transition = wait_spacing(channel, "ON", bsfs=bsfs)
        deadline = time.monotonic() + 90.0
        last_error: str | None = None
        while time.monotonic() < deadline:
            try:
                listing = list_gate(channel, bsfs)
                break
            except SessionError as exc:
                last_error = str(exc)
                time.sleep(1.0)
        else:
            raise SessionError(
                f"five peers did not recover after SPACING ON: {last_error}"
            )
    aggregate = listing["aggregate"]
    if (
        aggregate.get("spacing") != "ON"
        or aggregate.get("spacing_us") != "10000"
    ):
        raise SessionError(f"spacing gate failed: {aggregate}")
    return {"transition": transition, "list": listing}


def phase_respace(
    channel: LineChannel, bsfs: tuple[str, ...]
) -> dict[str, object]:
    before = list_gate(channel, bsfs)
    generation_before = int(before["aggregate"].get("spacing_generation", "0"))
    off = wait_spacing(channel, "OFF", bsfs=bsfs)
    on = wait_spacing(channel, "ON", bsfs=bsfs)
    after = list_gate(channel, bsfs)
    generation_after = int(after["aggregate"].get("spacing_generation", "0"))
    if generation_after <= generation_before:
        raise SessionError(
            "spacing generation did not advance: "
            f"{generation_before}->{generation_after}"
        )
    return {
        "before": before,
        "off": off,
        "on": on,
        "after": after,
        "generation_before": generation_before,
        "generation_after": generation_after,
    }


def phase_configure(
    channel: LineChannel,
    epoch: int,
    bsfs: tuple[str, ...],
    tag_ids: dict[str, int],
    slot_map: dict[str, int],
) -> dict[str, object]:
    spacing = _ensure_spacing(channel, bsfs)
    identity: dict[str, object] = {}
    for node in bsfs:
        # Stop ranging before the UART identity/readback sequence.  Querying
        # the tag while it is servicing a 10 Hz sweep can starve the relay on
        # marginal UART links; MODE IDLE is the established safe stop and the
        # complete CFG below restores the schedule.
        idle = relay_command(
            channel, node, "MODE IDLE", "MODE_OK MODE=IDLE", attempts=3
        )
        ping = b306_command(channel, node, "PING", "PONG ")
        if f"fw={EXPECTED_B306}" not in ping["text"]:
            raise SessionError(f"{node} B306 marker mismatch: {ping['text']}")
        version = relay_command(
            channel, node, "VERSION", "VERSION ", attempts=3
        )
        version_text = version["reply"]["text"]
        if f"fw={EXPECTED_TAG}" not in version_text:
            raise SessionError(f"{node} tag marker mismatch: {version_text}")
        if "cir=off" not in version_text.lower():
            relay_command(channel, node, "CIR OFF", "CIR_MODE OFF", attempts=3)
            version = relay_command(
                channel, node, "VERSION", "VERSION ", attempts=3
            )
            version_text = version["reply"]["text"]
            if "cir=off" not in version_text.lower():
                raise SessionError(f"{node} CIR did not reach OFF: {version_text}")
        imgstat = relay_command(
            channel, node, "IMGSTAT", "IMGSTAT ", attempts=3
        )
        image_text = imgstat["reply"]["text"]
        if EXPECTED_IMGSTAT not in image_text or "confirmed=1" not in image_text:
            raise SessionError(f"{node} IMGSTAT mismatch: {image_text}")
        identity[node] = {
            "idle": idle,
            "ping": ping,
            "version": version,
            "imgstat": imgstat,
        }

    cfg: dict[str, object] = {}
    for node in bsfs:
        tag = tag_ids[node]
        slot = slot_map[node]
        text = (
            f"CFG TAG={tag} SLOT={slot} COUNT=10 PERIOD=10 ACTIVE=9 "
            f"EPOCH={epoch & 0xFFFFFFFF} BEACON_SYNC=1 "
            "BEACON_WIN_N=1 DW_ANCHOR=0"
        )
        reply = relay_command(channel, node, text, "CFG_OK ", attempts=3)
        reply_text = reply["reply"]["text"]
        prefix_required = (
            f"TAG={tag}",
            f"SLOT={slot}/10",
            "PERIOD=10",
        )
        missing = [
            token for token in prefix_required if token not in reply_text
        ]
        if missing:
            raise SessionError(
                f"{node} CFG reply missing {missing}: {reply_text}"
            )
        full_required = (
            "ACTIVE=9",
            "BEACON_SYNC=1",
            "BEACON_WIN_N=1",
            "DW_ANCHOR=0",
            "LIVE=1",
            "RUN=1",
            "STATE=RUNNING",
        )
        cfg[node] = {
            "command": text,
            "reply": reply,
            "full_echo": all(token in reply_text for token in full_required),
            "independent_runtime_gate": (
                "BEACON_STATUS lock plus formal UWB stream"
            ),
        }

    status: dict[str, object] = {}
    deadline = time.monotonic() + 60.0
    pending = set(bsfs)
    attempts: dict[str, list[object]] = {node: [] for node in bsfs}
    while pending and time.monotonic() < deadline:
        for node in tuple(pending):
            try:
                reply = relay_command(
                    channel, node, "BEACON_STATUS", "BEACON ", attempts=1
                )
            except SessionError as exc:
                attempts[node].append({"status": "TIMEOUT", "error": str(exc)})
                continue
            attempts[node].append(reply)
            text = reply["reply"]["text"]
            required = (
                "sync=1",
                "lock=1",
                "promoted=0",
                "win=1",
                "dw=0",
            )
            if all(token in text for token in required):
                status[node] = reply
                pending.remove(node)
        if pending:
            time.sleep(1.0)
    if pending:
        raise SessionError(
            f"BEACON_STATUS lock timeout for {sorted(pending)}: {attempts}"
        )
    return {
        "spacing": spacing,
        "identity": identity,
        "epoch": epoch & 0xFFFFFFFF,
        "cfg": cfg,
        "beacon_status": status,
        "status_attempts": attempts,
    }


def phase_imu_start(
    channel: LineChannel, bsfs: tuple[str, ...]
) -> dict[str, object]:
    listing = list_gate(channel, bsfs)
    replies: dict[str, object] = {}
    for node in bsfs:
        rate = b306_command(channel, node, "IMU RATE=200", "IMU RATE OK ")
        batch = b306_command(channel, node, "IMU BATCH=5", "IMU BATCH OK ")
        start = b306_command(channel, node, "IMU START", "IMU START OK ")
        text = start["text"]
        required = (
            "61=0001:P",
            "03=000B:P",
            "1F=0002:P",
            "volatile=1",
            "saved=0",
        )
        if any(token not in text for token in required):
            raise SessionError(f"{node} IMU START verification failed: {text}")
        replies[node] = {"rate": rate, "batch": batch, "start": start}
    return {"list": listing, "imu_start": replies}


def phase_imu_stop(
    channel: LineChannel, bsfs: tuple[str, ...]
) -> dict[str, object]:
    replies: dict[str, object] = {}
    for node in bsfs:
        stop = b306_command(channel, node, "IMU STOP", "IMU STOP OK ")
        status = b306_command(channel, node, "IMU STATUS", "IMU ")
        if "active=0 " not in f"{status['text']} ":
            raise SessionError(f"{node} IMU still active: {status['text']}")
        replies[node] = {"stop": stop, "status": status}
    return {"imu_stop": replies}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=("idle", "respace", "configure", "imu-start", "imu-stop"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fusion-port")
    parser.add_argument("--epoch", type=int, default=5000)
    parser.add_argument(
        "--profile",
        choices=tuple(PROFILES),
        default="old5",
        help="select the established or newly commissioned five-node set",
    )
    parser.add_argument(
        "--g3-disc-swap",
        action="store_true",
        help="Preserve TAG IDs and exchange only BSFC2CC/BSF8BC4 slots 2/5.",
    )
    args = parser.parse_args()
    bsfs, default_slot_map, tag_ids = PROFILES[args.profile]
    if args.g3_disc_swap and args.profile != "old5":
        raise SystemExit("--g3-disc-swap is only defined for --profile old5")

    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "phase": args.phase,
        "started_utc": utc_now(),
    }
    log_path = args.output.with_suffix(".cdc.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists() or log_path.exists():
        raise SystemExit(f"refusing existing output/log: {args.output}")

    channel: LineChannel | None = None
    try:
        with log_path.open("w", encoding="utf-8", buffering=1) as log:
            channel = LineChannel(
                resolve_fusion_port(args.fusion_port), log, "FUSION"
            )
            result["port"] = channel.port
            # The master already emits host-binary.  Select the matching host
            # decoder locally, prove one known record, and only then transmit
            # the idempotent output-mode command.  This preserves the permanent
            # decode-before-send rule even for a Master-local command.
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_before_send"] = decode_guard(channel)
            result["initial_binary_resync_errors"] = (
                channel.binary_decoder.errors
            )
            channel.send("OUTPUT BINARY")
            if args.phase == "idle":
                result["result"] = phase_idle(channel, bsfs)
            elif args.phase == "respace":
                result["profile"] = args.profile
                result["result"] = phase_respace(channel, bsfs)
            elif args.phase == "configure":
                slot_map = dict(default_slot_map)
                if args.g3_disc_swap:
                    slot_map["BSFC2CC"] = 5
                    slot_map["BSF8BC4"] = 2
                result["slot_map"] = slot_map
                result["profile"] = args.profile
                result["result"] = phase_configure(
                    channel,
                    args.epoch,
                    bsfs,
                    tag_ids,
                    slot_map,
                )
            elif args.phase == "imu-start":
                result["result"] = phase_imu_start(channel, bsfs)
            else:
                result["result"] = phase_imu_stop(channel, bsfs)
        result["status"] = "PASS"
    except Exception as exc:
        result["status"] = "FAIL"
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if channel is not None:
            channel.close()
        result["ended_utc"] = utc_now()
        write_json(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
