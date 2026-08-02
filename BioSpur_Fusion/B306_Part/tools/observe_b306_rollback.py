#!/usr/bin/env python3
"""Read-only witness for one B306 MCUboot auto-revert transition."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import LineChannel, SessionError, resolve_fusion_port


MASTER_MARKER = "dk-fusion-imu-relay-v28"
PROOF_MARKER = "b306-v32-noconfirm-proof"
ROLLBACK_MARKER = "b306-imu-relay-v31"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--timeout-s", type=float, default=240.0)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {
        "node": args.node,
        "started": now(),
        "proof_marker": PROOF_MARKER,
        "rollback_marker": ROLLBACK_MARKER,
        "observations": [],
        "status": "IN_PROGRESS",
    }
    channel = None
    with (args.out_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = LineChannel(resolve_fusion_port(args.fusion_port), log, "FUSION")
            result["port"] = channel.port
            result["decode_before_send"] = decode_guard(channel, 15.0)
            master = wait_master_status(channel)
            result["master_status"] = master
            if f"marker={MASTER_MARKER}" not in master:
                raise SessionError(f"Fusion Master marker mismatch: {master}")

            proof_seen = False
            deadline = time.monotonic() + args.timeout_s
            while time.monotonic() < deadline:
                stamp = now()
                try:
                    ping = b306_command(channel, args.node, "PING", "PONG ")
                except SessionError as exc:
                    result["observations"].append({"at": stamp, "error": str(exc)})
                    time.sleep(2.0)
                    continue
                text = str(ping["text"])
                row: dict[str, object] = {"at": stamp, "ping": ping}
                result["observations"].append(row)
                if f"fw={PROOF_MARKER}" in text:
                    if not proof_seen:
                        proof_seen = True
                        status = b306_command(
                            channel, args.node, "BOOT CONFIRM STATUS",
                            "BOOT CONFIRM STATUS ",
                        )
                        row["boot_confirm_status"] = status
                    time.sleep(3.0)
                    continue
                if f"fw={ROLLBACK_MARKER}" in text:
                    if not proof_seen:
                        raise SessionError("v31 observed before proof marker was witnessed")
                    result["status"] = "PASS"
                    result["ended"] = now()
                    result["proof_seen"] = True
                    result["rollback_seen"] = True
                    return 0
                raise SessionError(f"unexpected marker: {text}")
            raise SessionError("rollback marker not observed before timeout")
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["ended"] = now()
            return 2
        finally:
            if channel is not None:
                channel.close()
            (args.out_dir / "rollback_observation.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )


if __name__ == "__main__":
    raise SystemExit(main())
