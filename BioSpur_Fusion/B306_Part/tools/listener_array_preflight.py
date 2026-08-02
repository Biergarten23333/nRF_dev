#!/usr/bin/env python3
"""Strict Batch-A five-node preflight without BLE parameter changes."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import BSFS, RecordingAssembler, b306_command, collect, relay_command
from fusion_session import FusionCdcChannel, SessionError, resolve_fusion_port
from post_capacity_qualification import strict_link_gate
from pre_ramp_hardening import request_list
from validate_layer2_v31 import (
    EXPECTED_B306_MARKER,
    EXPECTED_DK_MARKER,
    EXPECTED_TAG_MARKER,
    request_master_status,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run(channel: FusionCdcChannel) -> dict[str, object]:
    started_utc = utc_now()
    # Binary framing is required at five-node UWB load. Text records can be
    # concatenated on CDC and make a valid LIST reply unparseable.
    channel.send("OUTPUT BINARY")
    assembler = RecordingAssembler()
    counters: dict[str, int] = {}
    collect(channel, assembler, 2.0)
    listing = request_list(channel, assembler, counters, BSFS)
    strict_link_gate(listing)

    # Batch A freezes the established BLE parameters.  Unlike older runners,
    # this preflight records the current spacing but never changes it.

    master = request_master_status(channel)
    if master.get("marker") != EXPECTED_DK_MARKER:
        raise SessionError(f"DK marker mismatch: {master}")

    nodes: dict[str, object] = {}
    for bsf in BSFS:
        ping = b306_command(channel, bsf, "PING", "PONG ")
        ping_text = f"{ping['text']} "
        if (
            f"fw={EXPECTED_B306_MARKER} " not in ping_text
            or "proto=7 " not in ping_text
        ):
            raise SessionError(f"{bsf} B306 mismatch: {ping['text']}")

        tag = relay_command(channel, bsf, "VERSION", "VERSION ", attempts=3)
        tag_text = f"{tag['reply']['text']} "
        if f"fw={EXPECTED_TAG_MARKER} " not in tag_text:
            raise SessionError(f"{bsf} tag mismatch: {tag['reply']['text']}")

        imu = b306_command(channel, bsf, "IMU STATUS", "IMU ")
        if "active=0 " not in f"{imu['text']} ":
            raise SessionError(f"{bsf} IMU active before run: {imu['text']}")

        # CFG_STOP is deliberately absent. MODE IDLE is the only accepted stop.
        idle = relay_command(
            channel, bsf, "MODE IDLE", "MODE_OK MODE=IDLE", attempts=3
        )
        nodes[bsf] = {
            "ping": ping,
            "tag_version": tag,
            "imu_status": imu,
            "mode_idle": idle,
        }

    return {
        "status": "PASS",
        "started_utc": started_utc,
        "fusion_port": channel.port,
        "list": listing,
        "master": master,
        "nodes": nodes,
        "commands_excluded": ["CFG_STOP", "SPACING", "J-Link"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=False)
    port = resolve_fusion_port(args.port)
    with (args.output_dir / "fusion_cdc.log").open(
        "w", encoding="utf-8", buffering=1
    ) as log_file:
        channel = FusionCdcChannel(port, log_file, "FUSION")
        try:
            summary = run(channel)
        except Exception as exc:
            write_json(
                args.output_dir / "summary.json",
                {
                    "status": "FAILED",
                    "ended_utc": utc_now(),
                    "fusion_port": port,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise
        finally:
            channel.close()
    summary["ended_utc"] = utc_now()
    write_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
