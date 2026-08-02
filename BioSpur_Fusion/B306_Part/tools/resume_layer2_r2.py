#!/usr/bin/env python3
"""Resume only R2 after an evidence-backed R1c offline adjudication."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from capacity_ramp import BSFS, RecordingAssembler, b306_command, cleanup, collect, relay_command
from fusion_session import LineChannel, SessionError, resolve_fusion_port
from post_capacity_qualification import strict_link_gate
from pre_ramp_hardening import request_list
from validate_layer2_v31 import (
    EXPECTED_B306_MARKER,
    EXPECTED_DK_MARKER,
    EXPECTED_TAG_MARKER,
    request_master_status,
    run_and_close,
    utc_now,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--operator-token", required=True)
    parser.add_argument("--duration-s", type=float, default=1800.0)
    args = parser.parse_args()
    if args.operator_token != "POWERED ON":
        raise SessionError("literal operator token POWERED ON is required")
    summary_path = args.output_dir / "summary.json"
    adjudication_path = args.output_dir / "R1C_DELIVERED_RATE_ADJUDICATION.md"
    if not summary_path.is_file() or not adjudication_path.is_file():
        raise SessionError("existing summary and R1c adjudication are required")
    summary = json.loads(summary_path.read_text())
    if summary.get("R2", {}).get("status") != "NOT_RUN_R1C_FAILED":
        raise SessionError(f"R2 is not resumable from state {summary.get('R2')}")

    channel = None
    raw = None
    try:
        raw = (args.output_dir / "fusion_raw.log").open(
            "a", buffering=1, encoding="utf-8"
        )
        channel = LineChannel(
            resolve_fusion_port(args.fusion_port), raw, "FUSION"
        )
        channel.send("OUTPUT BINARY")
        assembler = RecordingAssembler()
        collect(channel, assembler, 2.0)
        preflight = request_list(channel, assembler, {}, BSFS)
        strict_link_gate(preflight)
        aggregate = preflight["aggregate"]
        if (
            aggregate.get("spacing") != "ON"
            or aggregate.get("spacing_us") != "10000"
        ):
            raise SessionError(f"R2 requires spacing ON/10000: {aggregate}")
        master_status = request_master_status(channel)
        if master_status.get("marker") != EXPECTED_DK_MARKER:
            raise SessionError(
                f"DK marker mismatch: {master_status.get('marker')}"
            )
        versions = {}
        for node in BSFS:
            ping = b306_command(channel, node, "PING", "PONG ")
            if f"fw={EXPECTED_B306_MARKER} " not in f"{ping['text']} ":
                raise SessionError(f"{node} marker mismatch: {ping['text']}")
            version = relay_command(
                channel, node, "VERSION", "VERSION ", attempts=3
            )
            if (
                f"fw={EXPECTED_TAG_MARKER} "
                not in f"{version['reply']['text']} "
            ):
                raise SessionError(
                    f"{node} tag mismatch: {version['reply']['text']}"
                )
            versions[node] = {"b306": ping, "tag": version}

        summary["R1c_adjudication"] = {
            "decision": "PASS",
            "reason": "binary telemetry records lacked legacy record= field",
            "corrected_delivered": 155994,
            "expected": 156000,
            "corrected_fraction": 155994 / 156000,
            "evidence": str(adjudication_path),
            "fixed_before_R2": True,
        }
        summary["R2_resume"] = {
            "started_utc": utc_now(),
            "operator_gate": args.operator_token,
            "preflight": preflight,
            "master_status": master_status,
            "versions": versions,
        }
        write_json(summary_path, summary)
        summary["R2"] = run_and_close(
            args.output_dir,
            channel,
            "R2_spacing_on",
            args.duration_s,
            int(time.time()) & 0xFF,
            "ON",
            require_zero_qdrop=True,
        )
        summary["R2_resume"]["completed_utc"] = utc_now()
        summary["status"] = "COMPLETE"
        summary["completed_utc"] = utc_now()
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["R2_resume_error"] = str(exc)
        summary["completed_utc"] = utc_now()
        if channel is not None:
            try:
                summary["R2_resume_cleanup"] = cleanup(channel)
            except Exception as cleanup_exc:
                summary["R2_resume_cleanup_error"] = str(cleanup_exc)
        raise
    finally:
        if channel is not None:
            channel.close()
        if raw is not None:
            raw.close()
        write_json(summary_path, summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
