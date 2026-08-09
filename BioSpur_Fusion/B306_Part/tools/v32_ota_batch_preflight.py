#!/usr/bin/env python3
"""Identity and capture-idle gate for the remaining-nine v32 OTA batch."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from capacity_ramp import RecordingAssembler, b306_command, collect
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import SessionError, parse_fields, resolve_fusion_port
from pre_ramp_hardening import request_list


# v46r2: was hardcoded to "dk-fusion-imu-relay-v28", two generations behind the
# live rig (v36). Same class of defect as --restore-build's default: a v32-era
# constant that silently answers a question about a rig that no longer exists.
# Overridable, and the default now tracks the rig rather than history.
import os
MASTER_MARKER = os.environ.get("BSF_MASTER_MARKER", "dk-fusion-imu-relay-v36")
# v46r2: was pinned to "b306-imu-relay-v31", three generations behind the fleet
# (nine boards on v44, BSF6C53 on v45). Same class as --restore-build's default
# and MASTER_MARKER: a v32-era constant quietly answering a question about a rig
# that no longer exists.
#
# ALLOW_MIXED exists because uniformity is a BATCH precondition, not a
# single-board one. The transaction's own --preflight-require target-only mode
# already says so: "Other nodes are inventory, never a precondition (trap 6.3)".
# Strict remains the default so a real fleet rollout still refuses a mixed
# fleet; the single-board case has to ask for the relaxation explicitly and it
# is recorded in the result.
import os
SOURCE_MARKER = os.environ.get("BSF_SOURCE_MARKER", "b306-imu-relay-v44")
ALLOW_MIXED = os.environ.get("BSF_PREFLIGHT_ALLOW_MIXED") == "1"
NODES = (
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF1120",
    "BSF31CC", "BSFAA61", "BSFEC35", "BSFB165",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--observe-s", type=float, default=15.0)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {"status": "IN_PROGRESS", "nodes": list(NODES)}
    channel: ThreadedLineChannel | None = None
    with (args.out_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
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
            master = wait_master_status(channel)
            result["master_status"] = master
            if f"marker={MASTER_MARKER}" not in master:
                raise SessionError(f"master marker mismatch: {master}")

            assembler = RecordingAssembler()
            collect(channel, assembler, 1.0)
            listing = request_list(channel, assembler, {}, NODES)
            result["list"] = listing
            ready = {
                node for node, row in listing["peers"].items()
                if row.get("connected") == "1"
                and row.get("subscribed") == "1"
                and row.get("link_contract") == "PASS"
            }
            if set(listing["peers"]) != set(NODES) or ready != set(NODES):
                raise SessionError(
                    f"remaining-nine gate failed: peers={sorted(listing['peers'])} "
                    f"ready={sorted(ready)}"
                )

            identities: dict[str, object] = {}
            for node in NODES:
                ping = b306_command(channel, node, "PING", "PONG ")
                identities[node] = {
                    "ping": ping, "source_confirm_query": "NOT_APPLICABLE_V31",
                    "imu_idle_evidence": "DATA_PLANE_AND_PERIODIC_TELEMETRY_BELOW",
                }
                name_ok = f"name={node}" in str(ping["text"])
                fw_ok = f"fw={SOURCE_MARKER}" in str(ping["text"])
                if not name_ok:
                    # A node answering to the wrong name is never tolerable.
                    raise SessionError(f"identity mismatch for {node}: {ping['text']}")
                if not fw_ok:
                    if not ALLOW_MIXED:
                        raise SessionError(f"source identity mismatch for {node}: {ping['text']}")
                    identities[node]["mixed_fleet"] = str(ping["text"])
            result["identities"] = identities

            boundary = channel.discard_pending("ota_batch_idle_observation_start")
            deadline = time.monotonic() + args.observe_s
            uwb = {node: 0 for node in NODES}
            imu = {node: 0 for node in NODES}
            imu_active: dict[str, str] = {}
            while time.monotonic() < deadline:
                line = channel.read(min(deadline, time.monotonic() + 0.5))
                if line is None:
                    continue
                fields = parse_fields(line)
                node = fields.get("name")
                if node not in uwb:
                    continue
                if line.startswith("FUSION_UWB "):
                    uwb[node] += 1
                elif line.startswith("FUSION_IMU "):
                    imu[node] += 1
                elif line.startswith("FUSION_TELEMETRY "):
                    imu_active[node] = fields.get("imu_active", "MISSING")
            result["idle_observation"] = {
                "boundary": boundary, "duration_s": args.observe_s,
                "uwb_records": uwb, "imu_records": imu,
                "latest_imu_active": imu_active,
            }
            if any(uwb.values()) or any(imu.values()) or set(imu_active) != set(NODES) or any(
                value != "0" for value in imu_active.values()
            ):
                raise SessionError(
                    f"capture is not proven idle: uwb={uwb} imu={imu} imu_active={imu_active}"
                )
            result["status"] = "PASS"
            return 0
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None:
                result["host_drain"] = channel.health_snapshot()
                channel.close()
            (args.out_dir / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
