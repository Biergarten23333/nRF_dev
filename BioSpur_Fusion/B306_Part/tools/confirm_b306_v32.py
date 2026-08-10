#!/usr/bin/env python3
"""Durably verify and, when necessary, confirm one exact B306 OTA payload."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import LineChannel, SessionError, resolve_fusion_port
from ota_build_identity import SCHEMA
from ota_confirmation import ConfirmationTimeout, ExpectedIdentity, confirm_until_durable

TOKEN_RE = re.compile(r"\btoken=([0-9A-F]{8})\b")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def extract_token(text: str) -> str:
    """Compatibility helper retained for existing source-contract tests."""
    match = TOKEN_RE.search(text)
    if match is None:
        raise SessionError(f"confirmation token absent from reply: {text}")
    return match.group(1)


def wait_master_status(channel: LineChannel, timeout_s: float = 5.0) -> str:
    import time
    channel.send("MASTER STATUS")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is not None and line.startswith("FUSION_MASTER_STATUS "):
            return line
    raise SessionError("MASTER STATUS timed out")


def load_identity(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != SCHEMA:
        raise ValueError(f"unsupported identity manifest: {value.get('schema')!r}")
    for key in ("fwid", "signed_payload_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(value.get(key, ""))):
            raise ValueError(f"manifest has invalid {key}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--identity-manifest", required=True, type=Path)
    parser.add_argument("--expected-master-marker", required=True)
    parser.add_argument("--deadline-s", required=True, type=float,
                        help="measured, documented pre-confirm deadline (< firmware bound)")
    parser.add_argument("--fusion-port")
    parser.add_argument("--target-only", action="store_true",
                        help="compatibility no-op: confirmation is always target-scoped")
    args = parser.parse_args()
    if not 0 < args.deadline_s < 180.0:
        parser.error("--deadline-s must be positive and below firmware's 180 s bound")
    identity = load_identity(args.identity_manifest)
    args.out_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {
        "schema": "biospur-ota-confirm-result-v1", "status": "IN_PROGRESS",
        "started": now(), "node": args.node, "identity_manifest": str(args.identity_manifest),
        "expected_fwid": identity["fwid"],
        "expected_payload_sha256": identity["signed_payload_sha256"],
        "deadline_s": args.deadline_s,
    }
    channel: LineChannel | None = None
    with (args.out_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = LineChannel(resolve_fusion_port(args.fusion_port), log, "FUSION")
            result["port"] = channel.port
            result["decode_before_send"] = decode_guard(channel, 15.0)
            master = wait_master_status(channel)
            result["master_status"] = master
            if f"marker={args.expected_master_marker}" not in master:
                raise SessionError(f"Fusion Master marker mismatch: {master}")

            def ping() -> str:
                return str(b306_command(channel, args.node, "PING", "PONG ")["text"])

            def status() -> str:
                return str(b306_command(channel, args.node, "BOOT CONFIRM STATUS",
                                         "BOOT CONFIRM STATUS ")["text"])

            def prepare_commit() -> None:
                prepared = b306_command(channel, args.node, "BOOT CONFIRM PREPARE",
                                         "BOOT CONFIRM PREPARED ")
                token = extract_token(str(prepared["text"]))
                committed = b306_command(channel, args.node,
                                         f"BOOT CONFIRM COMMIT={token}",
                                         "BOOT CONFIRM COMMIT OK ")
                result["prepare"] = prepared
                result["commit"] = committed

            state, samples = confirm_until_durable(
                ExpectedIdentity(args.node, str(identity["fwid"]),
                                 str(identity["signed_payload_sha256"])),
                ping, status, prepare_commit, deadline_s=args.deadline_s,
            )
            result["board_state"] = state.value
            result["samples"] = samples
            result["status"] = "PASS" if state.value == "TARGET_CONFIRMED" else "FAIL"
            return 0 if result["status"] == "PASS" else 2
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, ConfirmationTimeout):
                result["board_state"] = exc.state.value
                result["samples"] = exc.samples
            raise
        finally:
            if channel is not None:
                channel.close()
            result["ended"] = now()
            (args.out_dir / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
