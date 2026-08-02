#!/usr/bin/env python3
"""One bounded B306 reboot discriminator for BSF3C79's UART command path."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_overnight_core import composed_idle_cfg
from capacity_ramp import RecordingAssembler, relay_command
from coldstart_fusion_control import decode_guard
from fusion_session import FusionController, SessionError, parse_fields, resolve_fusion_port
from pre_ramp_hardening import request_list


NODE = "BSF3C79"
MARKER = "dk-fusion-imu-relay-v29"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--port")
    parser.add_argument("--skip-reboot", action="store_true")
    args = parser.parse_args()
    if args.evidence_root.exists():
        raise SystemExit(f"refusing existing evidence root: {args.evidence_root}")
    args.evidence_root.mkdir(parents=True)
    raw = (args.evidence_root / "fusion_cdc.log").open(
        "x", encoding="utf-8", buffering=1
    )
    channel = ThreadedLineChannel(
        resolve_fusion_port(args.port), raw, "FUSION", stall_red_s=1.0
    )
    channel.transport_mode = "binary"
    channel.text_pending.clear()
    result: dict[str, object] = {
        "node": NODE,
        "action": (
            "post-physical-cycle UART discriminator"
            if args.skip_reboot
            else "B306-only REBOOT"
        ),
    }
    try:
        result["decode_before_send"] = decode_guard(channel, 15.0)
        channel.send("MASTER STATUS")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            line = channel.read(deadline)
            if line and line.startswith("FUSION_MASTER_STATUS "):
                if parse_fields(line).get("marker") != MARKER:
                    raise SessionError(f"master marker mismatch: {line}")
                result["master_status"] = line
                break
        else:
            raise SessionError("missing Fusion Master status")
        listing = request_list(channel, RecordingAssembler(), {}, (NODE,))
        peer = listing["peers"].get(NODE, {})
        if peer.get("connected") != "1" or peer.get("subscribed") != "1":
            raise SessionError(f"{NODE} is not connected/subscribed: {peer}")
        result["list_before"] = listing
        controller = FusionController(channel, NODE, timeout_s=8.0, max_attempts=1)
        controller.ensure_bridge()
        if not args.skip_reboot:
            result["post_reboot_telemetry"] = controller.reboot_preflight()
        result["idle"] = relay_command(
            channel,
            NODE,
            composed_idle_cfg(1, 1, 11),
            "CFG_OK ",
            attempts=3,
        )
        result["beacon_status"] = relay_command(
            channel, NODE, "BEACON_STATUS", "BEACON ", attempts=2
        )
        result["status"] = "PASS_UART_COMMAND_PATH_RESTORED"
        rc = 0
    except Exception as exc:
        result["status"] = "FAIL_UART_COMMAND_PATH_STILL_DEAD"
        result["error"] = f"{type(exc).__name__}: {exc}"
        rc = 2
    finally:
        result["host_drain"] = channel.health_snapshot()
        (args.evidence_root / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        channel.close()
        raw.close()
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
