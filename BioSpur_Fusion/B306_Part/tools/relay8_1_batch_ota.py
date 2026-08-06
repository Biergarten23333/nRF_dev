#!/usr/bin/env python3
"""Sequential deployment-only relay8.1 OTA for all ten Fusion tags.

Operator-facing output contains B306 BSF identities only.  DWM advertising
identities remain confined to this tool and raw OTA evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_control import relay_command_patient
from batch_g_overnight_core import composed_idle_cfg
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from relay8_tag_control import wait_master_status
from relay8_batch_ota import (
    CONTROL,
    TAG_MASTER,
    OTA,
    load_result,
    witness_no_target_uwb,
    wait_target_uwb,
)


ROOT = Path(__file__).resolve().parents[2]
OLD_MARKER = "tag-fusion-link-relay8"
OLD_HASH = "69f8b6a1e4718d84156c8dbceb630fa578bf6d3d78ccec82da9cac5b6859bb26"
NEW_MARKER = "tag-fusion-link-relay8.1"
NEW_HASH = "d400780640816617ecd8ac53a86ece4a157cf17f1d17e81613f1d965402f3da5"
BSF44AD_DISCONTINUITY_EPOCH = 1785622873.189523

# BSFB165 stays last because its command path has the established long-latency
# behavior.  The second tuple item is never printed to the operator console.
ORDER = (
    ("BSF3C79", "BS065F"),
    ("BSFC2CC", "BSE88E"),
    ("BSF44AD", "BS6F3A"),
    ("BSF6C53", "BSF8E0"),
    ("BSF8BC4", "BSEFD2"),
    ("BSF1120", "BSB10B"),
    ("BSF31CC", "BS8251"),
    ("BSFAA61", "BSF572"),
    ("BSFEC35", "BSDB1B"),
    ("BSFB165", "BS1150"),
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_ledger(root: Path, ledger: list[dict[str, object]]) -> None:
    (root / "ledger.json").write_text(
        json.dumps(ledger, indent=2) + "\n", encoding="utf-8"
    )


def assert_image(query_result: dict[str, object], marker: str, image_hash: str) -> None:
    query = query_result["query"]
    version = query["version"]["reply"]["text"]
    imgstat = query["imgstat"]["reply"]["text"]
    if f"fw={marker}" not in version:
        raise RuntimeError(f"marker mismatch: expected {marker}")
    if image_hash not in imgstat or "confirmed=1" not in imgstat:
        raise RuntimeError(f"IMGSTAT/confirmation mismatch for {marker}")


def run_ota(target: str, board: Path) -> dict[str, object]:
    ota_dir = board / "ota"
    with (board / "ota_launcher.stdout.log").open(
        "x", encoding="utf-8", buffering=1
    ) as output:
        completed = subprocess.run(
            [
                sys.executable,
                str(OTA),
                "--port",
                TAG_MASTER,
                "--target-name",
                target,
                "--out-dir",
                str(ota_dir),
                "--timeout-s",
                "300",
                "--reconnect-timeout-s",
                "45",
            ],
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=420.0,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"OTA launcher rc={completed.returncode}")
    summary = json.loads((ota_dir / "summary.json").read_text(encoding="utf-8"))
    required_true = (
        "target_selection_ready",
        "phase_a_ok",
        "phase_b_ok",
        "ota_upload_complete_seen",
        "ota_pending_test_seen",
        "ota_reset_request_seen",
        "ota_success_seen",
        "controller_returned_to_recv",
    )
    if any(not summary.get(key) for key in required_true):
        raise RuntimeError("OTA transaction missing a mandatory success state")
    if summary.get("ota_wait_fail_seen") or summary.get("ota_gate_fail_seen"):
        raise RuntimeError("OTA transaction recorded a failed write/gate")
    if summary.get("ota_later_fail_seen"):
        raise RuntimeError("OTA transaction recorded a post-write failure")
    return summary


def control(action: str, node: str, out_dir: Path) -> dict[str, object]:
    """Run a full ten-peer control gate; no legacy offline exception."""
    with (out_dir.parent / f"{out_dir.name}.stdout.log").open(
        "x", encoding="utf-8", buffering=1
    ) as output:
        completed = subprocess.run(
            [
                sys.executable,
                str(CONTROL),
                action,
                "--node",
                node,
                "--out-dir",
                str(out_dir),
            ]
            + (["--reply-timeout-s", "120"] if action == "query" else []),
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=(
                300.0
                if action == "query"
                else (240.0 if node == "BSFB165" else 60.0)
            ),
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"{action} rc={completed.returncode}")
    return load_result(out_dir / "result.json")


def idle_with_fallback(node: str, board: Path) -> str:
    out_dir = board / "postota_idle"
    with (board / "postota_idle.stdout.log").open(
        "x", encoding="utf-8", buffering=1
    ) as output:
        completed = subprocess.run(
            [
                sys.executable,
                str(CONTROL),
                "idle",
                "--node",
                node,
                "--out-dir",
                str(out_dir),
            ],
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=240.0 if node == "BSFB165" else 60.0,
            check=False,
        )
    if completed.returncode == 0:
        load_result(out_dir / "result.json")
        return "echo_verified"
    witness_no_target_uwb(node, board / "postota_idle_behavioral_witness")
    return "behaviorally_verified_reply_missing"


def sweep_from_readiness(path: Path) -> int:
    result = json.loads((path / "result.json").read_text(encoding="utf-8"))
    fields = parse_fields(str(result.get("guard_record", "")))
    if "sweep" not in fields:
        raise RuntimeError(f"target sweep absent from {path}")
    return int(fields["sweep"], 0)


def wait_target_sweep_reboot(
    node: str,
    baseline_sweep: int,
    out_dir: Path,
    timeout_s: float = 90.0,
) -> dict[str, object]:
    """Pass only on the tag-own counter discontinuity caused by the new app boot."""
    out_dir.mkdir()
    result: dict[str, object] = {
        "node": node,
        "started": now(),
        "status": "IN_PROGRESS",
        "read_only": True,
        "baseline_sweep": baseline_sweep,
        "minimum_backward_step": 32,
    }
    channel: ThreadedLineChannel | None = None
    with (out_dir / "fusion_cdc.log").open(
        "x", encoding="utf-8", buffering=1
    ) as log:
        try:
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
            deadline = time.monotonic() + timeout_s
            prefix = f"FUSION_UWB proto=7 name={node} "
            while time.monotonic() < deadline:
                line = channel.read(deadline)
                if not line or not line.startswith(prefix):
                    continue
                fields = parse_fields(line)
                sweep = int(fields.get("sweep", str(baseline_sweep)), 0)
                if baseline_sweep - sweep > 32:
                    event_epoch = time.time()
                    result.update(
                        {
                            "status": "PASS",
                            "reboot_record": line,
                            "postboot_sweep": sweep,
                            "backward_step": baseline_sweep - sweep,
                            "discontinuity_epoch": event_epoch,
                        }
                    )
                    break
            if result["status"] != "PASS":
                raise RuntimeError(
                    f"{node} new-app sweep discontinuity not observed"
                )
            result["host_drain"] = channel.health_snapshot()
            if result["host_drain"]["red_markers"]:
                raise RuntimeError(f"{node} reboot witness drain RED")
            return result
        finally:
            if channel is not None:
                result.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            result["ended"] = now()
            (out_dir / "result.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )


def bounded_readback(
    node: str,
    out_dir: Path,
    *,
    discontinuity_epoch: float | None,
    poll_window_s: float = 360.0,
) -> dict[str, object]:
    """Poll every read-only query at 30 s cadence within one time bound."""
    out_dir.mkdir()
    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "node": node,
        "started": now(),
        "read_only": True,
        "poll_interval_s": 30.0,
        "poll_window_s": poll_window_s,
        "discontinuity_epoch": discontinuity_epoch,
        "ping_attempts": [],
        "version_attempts": [],
        "imgstat_attempts": [],
    }
    channel: ThreadedLineChannel | None = None
    with (out_dir / "fusion_cdc.log").open(
        "x", encoding="utf-8", buffering=1
    ) as log:
        try:
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
            # Post-OTA bounds are measured from the observed new-app sweep
            # discontinuity.  Pre-OTA inventory has no such event and uses a
            # bound measured from entry to this function.
            absolute_deadline_epoch = (
                discontinuity_epoch + poll_window_s
                if discontinuity_epoch is not None
                else time.time() + poll_window_s
            )
            result["absolute_deadline_epoch"] = absolute_deadline_epoch

            ping_start = time.monotonic()
            ping: dict[str, object] | None = None
            ping_attempt = 0
            while time.time() < absolute_deadline_epoch:
                ping_attempt += 1
                scheduled = ping_start + (ping_attempt - 1) * 30.0
                if time.monotonic() < scheduled:
                    time.sleep(scheduled - time.monotonic())
                tx_epoch = time.time()
                ping_row: dict[str, object] = {
                    "attempt": ping_attempt,
                    "tx_epoch": tx_epoch,
                    "delay_from_discontinuity_s": (
                        tx_epoch - discontinuity_epoch
                        if discontinuity_epoch is not None
                        else None
                    ),
                }
                try:
                    ping = b306_command(channel, node, "PING", "PONG ")
                    ping_row["reply_epoch"] = time.time()
                    ping_row["reply_delay_s"] = (
                        ping_row["reply_epoch"] - tx_epoch
                    )
                    ping_row["status"] = "ANSWERED"
                    result["ping_attempts"].append(ping_row)
                    break
                except Exception as exc:
                    ping_row["status"] = "NO_ANSWER"
                    ping_row["error"] = f"{type(exc).__name__}: {exc}"
                    result["ping_attempts"].append(ping_row)
            if ping is None:
                result["status"] = "NO_PING_WITHIN_BOUND"
                return result
            if f"name={node}" not in ping["text"]:
                raise RuntimeError(f"{node} PONG identity mismatch")
            result["ping"] = ping

            poll_start = time.monotonic()
            attempt = 0
            version: dict[str, object] | None = None
            while time.time() < absolute_deadline_epoch:
                attempt += 1
                scheduled = poll_start + (attempt - 1) * 30.0
                if time.monotonic() < scheduled:
                    time.sleep(scheduled - time.monotonic())
                tx_epoch = time.time()
                row: dict[str, object] = {
                    "attempt": attempt,
                    "tx_epoch": tx_epoch,
                    "delay_from_discontinuity_s": (
                        tx_epoch - discontinuity_epoch
                        if discontinuity_epoch is not None
                        else None
                    ),
                }
                try:
                    version = relay_command_patient(
                        channel,
                        node,
                        "VERSION",
                        "VERSION ",
                        attempts=1,
                        reply_timeout_s=min(
                            25.0,
                            max(1.0, absolute_deadline_epoch - time.time()),
                        ),
                    )
                    row["reply_epoch"] = time.time()
                    row["reply_delay_s"] = row["reply_epoch"] - tx_epoch
                    row["status"] = "ANSWERED"
                    result["version_attempts"].append(row)
                    break
                except Exception as exc:
                    row["status"] = "NO_ANSWER"
                    row["error"] = f"{type(exc).__name__}: {exc}"
                    result["version_attempts"].append(row)
            if version is None:
                result["status"] = "NO_VERSION_WITHIN_BOUND"
                return result

            # IMGSTAT is read-only and follows the same bounded polling
            # policy as VERSION.  State-changing writes remain zero-retry.
            result["query"] = {"version": version}
            imgstat_start = time.monotonic()
            imgstat: dict[str, object] | None = None
            imgstat_attempt = 0
            while time.time() < absolute_deadline_epoch:
                imgstat_attempt += 1
                scheduled = imgstat_start + (imgstat_attempt - 1) * 30.0
                if time.monotonic() < scheduled:
                    time.sleep(scheduled - time.monotonic())
                tx_epoch = time.time()
                row = {
                    "attempt": imgstat_attempt,
                    "tx_epoch": tx_epoch,
                    "delay_from_discontinuity_s": (
                        tx_epoch - discontinuity_epoch
                        if discontinuity_epoch is not None
                        else None
                    ),
                }
                try:
                    imgstat = relay_command_patient(
                        channel,
                        node,
                        "IMGSTAT",
                        "IMGSTAT ",
                        attempts=1,
                        reply_timeout_s=min(
                            25.0,
                            max(1.0, absolute_deadline_epoch - time.time()),
                        ),
                    )
                    row["reply_epoch"] = time.time()
                    row["reply_delay_s"] = row["reply_epoch"] - tx_epoch
                    row["status"] = "ANSWERED"
                    result["imgstat_attempts"].append(row)
                    break
                except Exception as exc:
                    row["status"] = "NO_ANSWER"
                    row["error"] = f"{type(exc).__name__}: {exc}"
                    result["imgstat_attempts"].append(row)
            if imgstat is None:
                result["status"] = "NO_IMGSTAT_WITHIN_BOUND"
                return result
            result["query"]["imgstat"] = imgstat
            result["first_version_success_delay_from_discontinuity_s"] = (
                result["version_attempts"][-1]["reply_epoch"]
                - discontinuity_epoch
                if discontinuity_epoch is not None
                else None
            )
            result["first_imgstat_success_delay_from_discontinuity_s"] = (
                result["imgstat_attempts"][-1]["reply_epoch"]
                - discontinuity_epoch
                if discontinuity_epoch is not None
                else None
            )
            # Backward-compatible field: command-path warm-up remains defined
            # as the first successful VERSION reply.
            result["first_success_delay_from_discontinuity_s"] = result[
                "first_version_success_delay_from_discontinuity_s"
            ]
            result["status"] = "PASS_READBACK"
            result["host_drain"] = channel.health_snapshot()
            if result["host_drain"]["red_markers"]:
                raise RuntimeError(f"{node} bounded readback drain RED")
            return result
        finally:
            if channel is not None:
                result.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            result["ended"] = now()
            (out_dir / "result.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )


def idle_once(node: str, board: Path) -> str:
    """One composed-CFG idle write; no resend after the command is queued."""
    out_dir = board / "postota_idle"
    out_dir.mkdir()
    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "node": node,
        "started": now(),
        "command_attempts": 1,
    }
    channel: ThreadedLineChannel | None = None
    with (out_dir / "fusion_cdc.log").open(
        "x", encoding="utf-8", buffering=1
    ) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION",
                decoded_queue_records=65536, backlog_red_records=8192,
                raw_backlog_red_bytes=8192, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_before_send"] = decode_guard(channel, 15.0)
            result["master_status"] = wait_master_status(channel)
            tag = dict((name, index) for index, (name, _) in enumerate(ORDER, 1))[node]
            command = composed_idle_cfg(tag, min(tag, 10), 11)
            result["command"] = command
            result["reply"] = relay_command_patient(
                channel, node, command, "CFG_OK ", attempts=1,
                reply_timeout_s=30.0,
            )
            result["status"] = "PASS"
            return "echo_verified"
        finally:
            if channel is not None:
                result["host_drain"] = channel.health_snapshot()
                channel.close()
            result["ended"] = now()
            (out_dir / "result.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("--start-at", choices=tuple(node for node, _ in ORDER))
    parser.add_argument(
        "--overnight",
        action="store_true",
        help=(
            "quarantine a failed board and continue; BSF44AD is readback-only"
        ),
    )
    args = parser.parse_args()
    root = args.evidence_root
    root.mkdir(parents=True, exist_ok=False)
    ledger: list[dict[str, object]] = []
    selected = list(ORDER)
    if args.start_at:
        selected = selected[
            next(i for i, row in enumerate(selected) if row[0] == args.start_at) :
        ]

    ordinal = {node: i for i, (node, _) in enumerate(ORDER, start=1)}
    for node, target in selected:
        try:
            board = root / f"{ordinal[node]:02d}_{node.lower()}"
            board.mkdir()
            row: dict[str, object] = {
                "node": node,
                "started": now(),
                "status": "IN_PROGRESS",
                "ota_write_retries": 0,
            }
            ledger.append(row)
            write_ledger(root, ledger)
            print(f"{node} START", flush=True)

            if args.overnight and node == "BSF44AD":
                # The upload/test/reset already completed before this run.
                # Tonight permits read-only classification only: no OTA and
                # no state-changing idle command on this board.
                after = bounded_readback(
                    node,
                    board / "readback_only",
                    discontinuity_epoch=BSF44AD_DISCONTINUITY_EPOCH,
                )
                if after.get("status") != "PASS_READBACK":
                    raise RuntimeError("BSF44AD did not answer bounded VERSION polling")
                assert_image(after, NEW_MARKER, NEW_HASH)
                row.update(
                    {
                        "ended": now(),
                        "status": "COMPLETE",
                        "marker": NEW_MARKER,
                        "imgstat_hash": NEW_HASH,
                        "confirmed": 1,
                        "ota_action": "none_readback_only",
                        "safe_state": "left_unchanged",
                        "first_success_delay_from_discontinuity_s": after.get(
                            "first_success_delay_from_discontinuity_s"
                        ),
                    }
                )
                write_ledger(root, ledger)
                print(f"{node} COMPLETE", flush=True)
                continue

            before = bounded_readback(
                node,
                board / "preota_query",
                discontinuity_epoch=None,
                poll_window_s=120.0,
            )
            if before.get("status") != "PASS_READBACK":
                raise RuntimeError(f"{node} pre-OTA readback unavailable")
            assert_image(before, OLD_MARKER, OLD_HASH)
            preota_uwb = board / "preota_target_uwb"
            wait_target_uwb(node, preota_uwb, timeout_s=30.0)
            baseline_sweep = sweep_from_readiness(preota_uwb)
            ota_summary = run_ota(target, board)

            # The B306/DK pipeline may still contain complete pre-reset UWB
            # records after OTA.  A generic record is therefore not app-boot
            # evidence.  Wait for relay8's tag-owned sweep to jump backward
            # relative to the pre-OTA baseline; that discontinuity identifies
            # the new application stream.
            reboot_witness = wait_target_sweep_reboot(
                node,
                baseline_sweep,
                board / "postota_new_app_reboot_witness",
                timeout_s=90.0,
            )
            settle_started = now()
            time.sleep(15.0)
            wait_target_uwb(
                node, board / "postota_app_alive_after_settle", timeout_s=45.0
            )
            row["postota_readiness_gate"] = {
                "preota_sweep": baseline_sweep,
                "postboot_sweep": reboot_witness["postboot_sweep"],
                "discontinuity_epoch": reboot_witness["discontinuity_epoch"],
                "new_app_discontinuity": True,
                "settle_started": settle_started,
                "settle_s": 15.0,
                "second_complete_uwb": True,
            }
            write_ledger(root, ledger)
            after = bounded_readback(
                node,
                board / "postota_query",
                discontinuity_epoch=float(reboot_witness["discontinuity_epoch"]),
            )
            if after.get("status") != "PASS_READBACK":
                raise RuntimeError(f"{node} post-OTA readback unavailable")
            assert_image(after, NEW_MARKER, NEW_HASH)

            # Keep each completed unit out of the live field while the remaining
            # units update.  This is the proven internally-valid composed idle,
            # never CFG_STOP and never the defective bare MODE IDLE path.
            try:
                idle_proof = idle_once(node, board)
            except Exception as idle_exc:
                witness_no_target_uwb(
                    node, board / "postota_idle_behavioral_witness"
                )
                idle_proof = (
                    "behaviorally_verified_reply_missing: "
                    f"{type(idle_exc).__name__}: {idle_exc}"
                )
            row.update(
                {
                    "ended": now(),
                    "status": "COMPLETE",
                    "marker": NEW_MARKER,
                    "imgstat_hash": NEW_HASH,
                    "confirmed": 1,
                    "ota_classification": ota_summary.get("classification"),
                    "first_success_delay_from_discontinuity_s": after.get(
                        "first_success_delay_from_discontinuity_s"
                    ),
                    "read_query_attempts": {
                        "preota_ping": len(before.get("ping_attempts", [])),
                        "preota_version": len(before.get("version_attempts", [])),
                        "preota_imgstat": len(before.get("imgstat_attempts", [])),
                        "postota_ping": len(after.get("ping_attempts", [])),
                        "postota_version": len(after.get("version_attempts", [])),
                        "postota_imgstat": len(after.get("imgstat_attempts", [])),
                    },
                    "safe_state": "composed_idle",
                    "idle_proof": idle_proof,
                }
            )
            write_ledger(root, ledger)
            print(f"{node} COMPLETE", flush=True)
        except Exception as exc:
            if ledger:
                ledger[-1].update(
                    {
                        "ended": now(),
                        "status": "QUARANTINED" if args.overnight else "FAIL",
                        "error": f"{type(exc).__name__}: {exc}",
                        "state_change_after_failure": "none",
                    }
                )
            write_ledger(root, ledger)
            if not args.overnight:
                print(f"BATCH STOP — {exc}", flush=True)
                return 2
            print(f"{node} QUARANTINED — continuing", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
