#!/usr/bin/env python3
"""Earn B306-v32 MCUboot confirmation through a two-command BLE round trip."""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import LineChannel, SessionError, resolve_fusion_port


MASTER_MARKER = "dk-fusion-imu-relay-v28"
B306_MARKER = "b306-imu-relay-v32"
TOKEN_RE = re.compile(r"\btoken=([0-9A-F]{8})\b")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def extract_token(text: str) -> str:
    match = TOKEN_RE.search(text)
    if match is None:
        raise SessionError(f"confirmation token absent from reply: {text}")
    return match.group(1)


def wait_master_status(channel: LineChannel, timeout_s: float = 5.0) -> str:
    channel.send("MASTER STATUS")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is not None and line.startswith("FUSION_MASTER_STATUS "):
            return line
    raise SessionError("MASTER STATUS timed out")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True, choices=(
        "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
        "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35",
    ))
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--ready-count", type=int, default=0)
    parser.add_argument("--ready-timeout-s", type=float, default=60.0)
    parser.add_argument(
        "--bridge-ready-timeout-s", type=float, default=180.0,
        help=(
            "bounded wait for this target's bridge after the updater reset; "
            "re-asks PING only while the Master answers bridge_not_ready"
        ),
    )
    parser.add_argument(
        "--target-only", action="store_true",
        help="confirm the named target without any fleet-wide readiness dependency",
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)

    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": now(),
        "node": args.node,
        "master_marker": MASTER_MARKER,
        "b306_marker": B306_MARKER,
    }
    channel: LineChannel | None = None
    with (args.out_dir / "fusion_cdc.log").open(
        "x", encoding="utf-8", buffering=1
    ) as log:
        try:
            channel = LineChannel(
                resolve_fusion_port(args.fusion_port), log, "FUSION"
            )
            result["port"] = channel.port
            result["decode_before_send"] = decode_guard(channel, 15.0)

            if args.target_only and args.ready_count:
                raise SessionError("--target-only and --ready-count are mutually exclusive")
            master = None if args.target_only else wait_master_status(channel)
            if args.ready_count and master is not None:
                ready_deadline = time.monotonic() + args.ready_timeout_s
                while f"ready={args.ready_count}" not in master:
                    if time.monotonic() >= ready_deadline:
                        raise SessionError(
                            f"master did not reach ready={args.ready_count}: {master}"
                        )
                    time.sleep(1.0)
                    master = wait_master_status(channel)
            if master is not None and f"marker={MASTER_MARKER}" not in master:
                raise SessionError(f"Fusion Master marker mismatch: {master}")
            result["master_status"] = master
            result["confirmation_scope"] = (
                "TARGET_ONLY" if args.target_only else "FLEET_GATED"
            )

            # Section 4.3 makes bridge readiness part of the per-target
            # confirmation contract, so wait for it instead of failing the whole
            # transaction on the first `bridge_not_ready` rejection. After the
            # updater's reset the peer needs a variable time to reconnect and
            # bring its bridge up; a fixed post-restore sleep races that. PING is
            # an idempotent read query, so re-asking is not an OTA write retry.
            ping = None
            bridge_deadline = time.monotonic() + args.bridge_ready_timeout_s
            bridge_waits = []
            while True:
                try:
                    ping = b306_command(channel, args.node, "PING", "PONG ")
                    break
                except SessionError as exc:
                    # N6: `reason=syntax` is retried on the same terms.
                    # The DK rejects at its `length < 9u` gate, i.e. it received
                    # a TRUNCATED line -- `BSF6C53 PING` is 12 characters. That
                    # is a CDC race in the seconds after the restore reflash
                    # re-enumerates, not a malformed command, and it stranded
                    # two correctly uploaded images before it was diagnosed.
                    # PING is an idempotent read query, so re-asking is not an
                    # OTA write retry, exactly as for bridge_not_ready above.
                    # V43: `reason=not_connected` on the SAME terms, and for the
                    # same reason. The updater resets the target into its new
                    # image; until that image advertises and the Master
                    # reconnects, the Master has no peer to route to and rejects
                    # with not_connected. That is the reconnect window, not a
                    # failed deployment -- the v43 canary hit it with the upload
                    # already reported MARKERS_COMPLETE, and treating it as fatal
                    # would quarantine a board whose image is correctly written.
                    # Still an idempotent read query, so still not a write retry.
                    retryable = ("bridge_not_ready" in str(exc)
                                 or "reason=syntax" in str(exc)
                                 or "reason=not_connected" in str(exc))
                    if not retryable:
                        raise
                    if time.monotonic() >= bridge_deadline:
                        raise SessionError(
                            f"bridge not ready within "
                            f"{args.bridge_ready_timeout_s}s: {exc}"
                        ) from exc
                    bridge_waits.append(round(time.monotonic(), 3))
                    time.sleep(2.0)
            if bridge_waits:
                result["bridge_ready_retries"] = len(bridge_waits)
            if f"fw={B306_MARKER}" not in str(ping["text"]):
                raise SessionError(f"B306 marker mismatch: {ping['text']}")
            result["ping"] = ping

            before = b306_command(
                channel, args.node, "BOOT CONFIRM STATUS", "BOOT CONFIRM STATUS "
            )
            result["before"] = before
            if "confirmed=1" in str(before["text"]):
                result["status"] = "ALREADY_CONFIRMED"
                return 0
            if "required=1" not in str(before["text"]):
                raise SessionError(f"image is not confirmable: {before['text']}")

            prepared = b306_command(
                channel, args.node, "BOOT CONFIRM PREPARE", "BOOT CONFIRM PREPARED "
            )
            token = extract_token(str(prepared["text"]))
            result["prepared"] = prepared
            result["token"] = token

            committed = b306_command(
                channel,
                args.node,
                f"BOOT CONFIRM COMMIT={token}",
                "BOOT CONFIRM COMMIT OK ",
            )
            result["committed"] = committed

            deadline = time.monotonic() + 15.0
            after = None
            while time.monotonic() < deadline:
                time.sleep(1.0)
                candidate = b306_command(
                    channel,
                    args.node,
                    "BOOT CONFIRM STATUS",
                    "BOOT CONFIRM STATUS ",
                )
                if "confirmed=1" in str(candidate["text"]):
                    after = candidate
                    break
            if after is None:
                raise SessionError("v32 did not confirm inside the 15 s host bound")
            result["after"] = after
            result["status"] = "PASS"
            return 0
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None:
                channel.close()
            result["ended"] = now()
            (args.out_dir / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
