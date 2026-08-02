#!/usr/bin/env python3
"""One-shot, read-only relay8.1 VERSION/IMGSTAT discriminator for BSF3C79."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from batch_g_control import relay_command_patient
from coldstart_fusion_control import decode_guard
from fusion_session import LineChannel, SessionError, parse_fields, resolve_fusion_port


NODE = "BSF3C79"
MASTER_MARKER = "dk-fusion-imu-relay-v29"
RELAY8_MARKER = "tag-fusion-link-relay8"
RELAY8_HASH = "69f8b6a1e4718d84156c8dbceb630fa578bf6d3d78ccec82da9cac5b6859bb26"
RELAY8_1_MARKER = "tag-fusion-link-relay8.1"
RELAY8_1_HASH = "d400780640816617ecd8ac53a86ece4a157cf17f1d17e81613f1d965402f3da5"


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--app-proof", type=Path, required=True)
    parser.add_argument("--reset-epoch", type=float, required=True)
    parser.add_argument("--patient-s", type=float, default=120.0)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)

    proof = json.loads(args.app_proof.read_text(encoding="utf-8"))
    guard_record = proof.get("guard_record", "")
    if proof.get("status") != "PASS" or not guard_record.startswith(
        f"FUSION_UWB proto=7 name={NODE} "
    ):
        raise SessionError("post-reboot app-plane UWB proof absent")

    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": stamp(),
        "node": NODE,
        "reset_epoch_estimate": args.reset_epoch,
        "reset_epoch_basis": (
            "OTA single_shot.log birth epoch + HOST_EVT 37.85 s; "
            "remote reset request precedes that host event"
        ),
        "post_reboot_app_proof": guard_record,
        "patient_window_s": args.patient_s,
        "writes": "none; VERSION and IMGSTAT are read-only queries",
    }
    channel: LineChannel | None = None
    with (args.out_dir / "fusion_cdc.log").open(
        "x", encoding="utf-8", buffering=1
    ) as raw:
        try:
            channel = LineChannel(resolve_fusion_port(None), raw, "FUSION")
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["port"] = channel.port
            result["decode_before_send"] = decode_guard(channel, 15.0)

            channel.send("MASTER STATUS")
            deadline = time.monotonic() + 5.0
            master = ""
            while time.monotonic() < deadline:
                line = channel.read(deadline)
                if line and line.startswith("FUSION_MASTER_STATUS "):
                    master = line
                    break
            if parse_fields(master).get("marker") != MASTER_MARKER:
                raise SessionError(f"Fusion Master identity mismatch: {master}")
            result["master_status"] = master

            version_call_epoch = time.time()
            result["version_query_call_epoch"] = version_call_epoch
            result["version_query_elapsed_from_reset_s"] = (
                version_call_epoch - args.reset_epoch
            )
            try:
                version = relay_command_patient(
                    channel,
                    NODE,
                    "VERSION",
                    "VERSION ",
                    attempts=1,
                    reply_timeout_s=args.patient_s,
                )
            except SessionError as exc:
                result.update(
                    {
                        "status": "NO_ANSWER_PATIENT_WINDOW",
                        "branch": "no_answer_wait_for_self_revert",
                        "version_error": str(exc),
                        "ended": stamp(),
                        "elapsed_from_reset_at_end_s": time.time()
                        - args.reset_epoch,
                    }
                )
                return 3

            result["version"] = version
            version_text = version["reply"]["text"]
            imgstat_call_epoch = time.time()
            result["imgstat_query_call_epoch"] = imgstat_call_epoch
            result["imgstat_query_elapsed_from_reset_s"] = (
                imgstat_call_epoch - args.reset_epoch
            )
            try:
                imgstat = relay_command_patient(
                    channel,
                    NODE,
                    "IMGSTAT",
                    "IMGSTAT ",
                    attempts=1,
                    reply_timeout_s=args.patient_s,
                )
            except SessionError as exc:
                result.update(
                    {
                        "status": "IMGSTAT_NO_ANSWER_PATIENT_WINDOW",
                        "branch": "partial_answer_no_intervention",
                        "imgstat_error": str(exc),
                        "ended": stamp(),
                        "elapsed_from_reset_at_end_s": time.time()
                        - args.reset_epoch,
                    }
                )
                return 4

            result["imgstat"] = imgstat
            imgstat_text = imgstat["reply"]["text"]
            confirmed = "confirmed=1" in imgstat_text
            if f"fw={RELAY8_1_MARKER}" in version_text:
                marker = RELAY8_1_MARKER
                expected_hash = RELAY8_1_HASH
                branch = "relay8_1_running"
            elif f"fw={RELAY8_MARKER}" in version_text:
                marker = RELAY8_MARKER
                expected_hash = RELAY8_HASH
                branch = "self_reverted_to_relay8"
            else:
                marker = "UNKNOWN"
                expected_hash = ""
                branch = "unexpected_marker"
            result.update(
                {
                    "marker": marker,
                    "expected_hash": expected_hash,
                    "imgstat_hash_matches": bool(
                        expected_hash and expected_hash in imgstat_text
                    ),
                    "confirmed": confirmed,
                    "branch": branch,
                    "status": "PASS_READBACK"
                    if marker != "UNKNOWN"
                    and expected_hash in imgstat_text
                    and confirmed
                    else "READBACK_MISMATCH_OR_UNCONFIRMED",
                    "ended": stamp(),
                    "elapsed_from_reset_at_end_s": time.time()
                    - args.reset_epoch,
                }
            )
            return 0 if result["status"] == "PASS_READBACK" else 5
        finally:
            if channel is not None:
                channel.close()
            result.setdefault("ended", stamp())
            (args.out_dir / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
