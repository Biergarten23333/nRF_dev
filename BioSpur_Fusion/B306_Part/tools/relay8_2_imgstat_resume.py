#!/usr/bin/env python3
"""Read-only BSF8BC4 IMGSTAT recovery after the S2 polling-spec correction."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_control import relay_command_patient
from coldstart_fusion_control import decode_guard
from fusion_session import resolve_fusion_port
from relay8_tag_control import wait_master_status


NODE = "BSF8BC4"
EXPECTED_HASH = "dacecc59e5b6fd8d1197e2f6ae57cb2673f1113f4f7902f81d64819190080d3f"
ORIGINAL_DISCONTINUITY_EPOCH = 1785667717.588431
ORIGINAL_FAILED_ATTEMPTS = 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    args = parser.parse_args()
    root = args.evidence_root
    root.mkdir(parents=True, exist_ok=False)

    started_epoch = time.time()
    result: dict[str, object] = {
        "node": NODE,
        "status": "IN_PROGRESS",
        "read_only": True,
        "state_changing_commands": 0,
        "original_discontinuity_epoch": ORIGINAL_DISCONTINUITY_EPOCH,
        "original_deadline_epoch": ORIGINAL_DISCONTINUITY_EPOCH + 360.0,
        "original_failed_imgstat_attempts": ORIGINAL_FAILED_ATTEMPTS,
        "resume_started_epoch": started_epoch,
        "resume_deadline_epoch": started_epoch + 360.0,
        "resume_window_reason": (
            "The explicit resume amendment arrived after the original "
            "discontinuity-relative deadline; this is the authorized fresh "
            "read-only recovery window."
        ),
        "cadence_s": 30.0,
        "attempts": [],
    }
    channel: ThreadedLineChannel | None = None
    try:
        with (root / "fusion_cdc.log").open(
            "x", encoding="utf-8", buffering=1
        ) as log:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None),
                log,
                "FUSION",
                decoded_queue_records=65536,
                backlog_red_records=8192,
                raw_backlog_red_bytes=8192,
                stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["port"] = channel.port
            result["decode_before_send"] = decode_guard(channel, 15.0)
            result["master_status"] = wait_master_status(channel)

            start_mono = time.monotonic()
            attempt = 0
            while time.time() < result["resume_deadline_epoch"]:
                attempt += 1
                scheduled = start_mono + (attempt - 1) * 30.0
                if time.monotonic() < scheduled:
                    time.sleep(scheduled - time.monotonic())
                tx_epoch = time.time()
                row: dict[str, object] = {
                    "resume_attempt": attempt,
                    "total_attempt_including_original": (
                        ORIGINAL_FAILED_ATTEMPTS + attempt
                    ),
                    "tx_epoch": tx_epoch,
                    "elapsed_from_resume_s": tx_epoch - started_epoch,
                    "elapsed_from_discontinuity_s": (
                        tx_epoch - ORIGINAL_DISCONTINUITY_EPOCH
                    ),
                }
                try:
                    reply = relay_command_patient(
                        channel,
                        NODE,
                        "IMGSTAT",
                        "IMGSTAT ",
                        attempts=1,
                        reply_timeout_s=min(
                            25.0,
                            max(
                                1.0,
                                float(result["resume_deadline_epoch"])
                                - time.time(),
                            ),
                        ),
                    )
                    row["reply_epoch"] = time.time()
                    row["reply_delay_s"] = row["reply_epoch"] - tx_epoch
                    row["status"] = "ANSWERED"
                    row["reply"] = reply
                    result["attempts"].append(row)
                    text = str(reply["reply"]["text"])
                    if EXPECTED_HASH not in text or "confirmed=1" not in text:
                        result["status"] = "ANSWERED_BUT_IMAGE_MISMATCH"
                        result["reply"] = text
                        return 2
                    result.update(
                        {
                            "status": "PASS",
                            "reply": text,
                            "canonical_hash": EXPECTED_HASH,
                            "confirmed": 1,
                            "resume_attempts": attempt,
                            "total_attempts_including_original": (
                                ORIGINAL_FAILED_ATTEMPTS + attempt
                            ),
                            "first_success_delay_from_resume_s": (
                                row["reply_epoch"] - started_epoch
                            ),
                        }
                    )
                    return 0
                except Exception as exc:
                    row["status"] = "NO_ANSWER"
                    row["error"] = f"{type(exc).__name__}: {exc}"
                    result["attempts"].append(row)
            result["status"] = "NO_ANSWER_WITHIN_6_MIN_RESUME_BOUND"
            return 2
    finally:
        if channel is not None:
            result["host_drain"] = channel.health_snapshot()
            channel.close()
        result["ended_epoch"] = time.time()
        (root / "result.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    raise SystemExit(main())
