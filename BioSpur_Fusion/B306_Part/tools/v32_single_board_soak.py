#!/usr/bin/env python3
"""R2 one-board v32 full-load mini-soak and terminal cleanup."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_control import relay_command_patient
from capacity_ramp import RecordingAssembler, b306_command, collect
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import SessionError, imu_sequence_gaps, parse_fields, resolve_fusion_port, u32_delta
from pre_ramp_hardening import request_list


MASTER_MARKER = "dk-fusion-imu-relay-v28"
B306_MARKER = "b306-imu-relay-v32"
TAG_NUMBER = {"BSF8BC4": 5}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def wait_queue(channel: ThreadedLineChannel, node: str, timeout_s: float = 5.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line and line.startswith("FUSION_QUEUE "):
            fields = parse_fields(line)
            if fields.get("name") == node:
                return fields
    raise SessionError(f"fresh queue counters missing for {node}")


def wait_beacon_lock(channel: ThreadedLineChannel, node: str) -> dict[str, object]:
    attempts: list[object] = []
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        try:
            reply = relay_command_patient(
                channel, node, "BEACON_STATUS", "BEACON ", attempts=1, reply_timeout_s=4.0
            )
        except SessionError as exc:
            attempts.append({"error": str(exc)})
            continue
        attempts.append(reply)
        text = str(reply["reply"]["text"])
        if all(token in text for token in ("sync=1", "lock=1", "promoted=0", "win=1", "dw=0")):
            return {"accepted": reply, "attempts": attempts}
        time.sleep(0.5)
    raise SessionError(f"beacon lock timeout: {attempts}")


def composed_cfg(node: str, *, running: bool) -> str:
    tag = TAG_NUMBER[node]
    if running:
        return (
            f"CFG TAG={tag} SLOT={tag} COUNT=10 PERIOD=10 ACTIVE=9 EPOCH=5000 "
            "BEACON_SYNC=1 BEACON_WIN_N=1 DW_ANCHOR=0"
        )
    return (
        f"CFG TAG={tag} SLOT={tag} COUNT=10 PERIOD=10 ACTIVE=9 EPOCH=5000 "
        "BEACON_SYNC=0 BEACON_WIN_N=1 DW_ANCHOR=0 RUN=0 PMODE=3"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True, choices=tuple(TAG_NUMBER))
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--duration-s", type=float, default=600.0)
    parser.add_argument("--idle-witness-s", type=float, default=90.0)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {
        "status": "IN_PROGRESS", "started": now(), "node": args.node,
        "duration_s": args.duration_s,
        "physical_arrangement": (
            f"{args.node} only powered, approximately 30 cm from DK 683234364; "
            "other nine boards docked/off"
        ),
    }
    channel: ThreadedLineChannel | None = None
    imu_started = False
    tag_running = False
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
            listing = request_list(channel, assembler, {}, (args.node,))
            result["list"] = listing
            if (
                listing["aggregate"].get("count") != "1"
                or listing["aggregate"].get("ready") != "1"
                or set(listing["peers"]) != {args.node}
            ):
                raise SessionError(f"single-peer gate failed: {listing}")

            ping = b306_command(channel, args.node, "PING", "PONG ")
            confirm = b306_command(channel, args.node, "BOOT CONFIRM STATUS", "BOOT CONFIRM STATUS ")
            result["identity"] = {"ping": ping, "confirm": confirm}
            if f"name={args.node}" not in str(ping["text"]) or f"fw={B306_MARKER}" not in str(ping["text"]):
                raise SessionError(f"identity/marker mismatch: {ping['text']}")
            if "confirmed=1" not in str(confirm["text"]):
                raise SessionError(f"v32 is not confirmed: {confirm['text']}")

            # Make the run idempotent after a host-side pre-formal failure.
            # A previous invocation may have delivered IMU START before its
            # own echo validator stopped, so always establish active=0 first.
            pre_stop = b306_command(channel, args.node, "IMU STOP", "IMU STOP OK ")
            pre_status = b306_command(channel, args.node, "IMU STATUS", "IMU ")
            result["imu_preflight_stop"] = {"stop": pre_stop, "status": pre_status}
            if "active=0 " not in f"{pre_status['text']} ":
                raise SessionError(f"IMU is active after preflight stop: {pre_status['text']}")

            run_cfg = composed_cfg(args.node, running=True)
            result["run_cfg"] = relay_command_patient(
                channel, args.node, run_cfg, "CFG_OK ", attempts=1, reply_timeout_s=15.0
            )
            run_ack = str(result["run_cfg"]["reply"]["text"])
            if any(token not in run_ack for token in (
                "TAG=5", "SLOT=5/10", "BEACON_SYNC=1", "BEACON_WIN_N=1",
                "DW_ANCHOR=0", "LIVE=1", "RUN=1", "STATE=RUNNING",
            )):
                raise SessionError(f"run CFG echo incomplete: {run_ack}")
            tag_running = True
            result["beacon_lock"] = wait_beacon_lock(channel, args.node)

            rate_reply = b306_command(channel, args.node, "IMU RATE=200", "IMU RATE OK ")
            batch_reply = b306_command(channel, args.node, "IMU BATCH=10", "IMU BATCH OK ")
            start_reply = b306_command(channel, args.node, "IMU START", "IMU START OK ")
            # From this point on, every exit must send IMU STOP even when an
            # echo validator below rejects the reply.
            imu_started = True
            result["imu_commands"] = {
                "rate": rate_reply,
                "batch": batch_reply,
                "start": start_reply,
            }
            if "n=10" not in str(result["imu_commands"]["batch"]["text"]):
                raise SessionError(f"batch=10 echo missing: {result['imu_commands']['batch']}")
            if any(token not in str(result["imu_commands"]["start"]["text"]) for token in (
                "61=0001:P", "03=000B:P", "1F=0002:P", "volatile=1", "saved=0",
            )):
                raise SessionError(f"IMU START verification failed: {result['imu_commands']['start']}")
            baseline = wait_queue(channel, args.node, 8.0)
            result["queue_before"] = baseline
            channel.discard_pending("r2_mini_soak_start")
            formal_start = time.monotonic()
            lines: list[str] = []
            deadline = formal_start + args.duration_s
            next_progress = formal_start + 60.0
            while time.monotonic() < deadline:
                line = channel.read(min(deadline, time.monotonic() + 0.5))
                if line is not None:
                    lines.append(line)
                if time.monotonic() >= next_progress:
                    imu_n = sum(1 for item in lines if item.startswith("FUSION_IMU "))
                    uwb_n = sum(1 for item in lines if item.startswith("FUSION_UWB "))
                    print(
                        f"R2_SOAK_PROGRESS elapsed_s={time.monotonic()-formal_start:.1f} "
                        f"imu_records={imu_n} uwb_records={uwb_n}", flush=True,
                    )
                    next_progress += 60.0
            formal_end = time.monotonic()
            final = wait_queue(channel, args.node, 8.0)
            result["queue_after"] = final

            imu_lines = [line for line in lines if line.startswith("FUSION_IMU ") and parse_fields(line).get("name") == args.node]
            uwb_lines = [line for line in lines if line.startswith("FUSION_UWB ") and parse_fields(line).get("name") == args.node]
            queue_lines = [line for line in lines if line.startswith("FUSION_QUEUE ") and parse_fields(line).get("name") == args.node]
            n_values = Counter(parse_fields(line).get("n") for line in imu_lines)
            imu_samples = sum(int(parse_fields(line).get("n", "0"), 0) for line in imu_lines)
            seq_gaps, _ = imu_sequence_gaps(imu_lines)
            duration = formal_end - formal_start
            q_delta = u32_delta(int(baseline["q_drop_imu"], 0), int(final["q_drop_imu"], 0))
            result["formal"] = {
                "started_monotonic": formal_start, "ended_monotonic": formal_end,
                "duration_s": duration, "imu_records": len(imu_lines),
                "imu_samples": imu_samples, "imu_rate_hz": imu_samples / duration,
                "imu_n_values": dict(n_values), "imu_sequence_gaps": seq_gaps,
                "uwb_records": len(uwb_lines), "uwb_rate_hz": len(uwb_lines) / duration,
                "queue_records": len(queue_lines), "q_drop_imu_delta": q_delta,
                "decoder_errors": channel.binary_decoder.errors,
                "host_drain": channel.health_snapshot(),
            }
            formal_pass = (
                q_delta == 0 and seq_gaps == 0 and set(n_values) == {"10"}
                and len(imu_lines) > 0 and 190.0 <= imu_samples / duration <= 210.0
                and 8.0 <= len(uwb_lines) / duration <= 12.0
                and channel.binary_decoder.errors == 0
                and channel.health_snapshot()["red_markers"] == 0
                and channel.health_snapshot()["decoded_queue_drops"] == 0
                and channel.health_snapshot()["log_queue_drops"] == 0
            )
            result["formal"]["pass"] = formal_pass
            if not formal_pass:
                raise SessionError(f"R2 mini-soak gates failed: {result['formal']}")

            stop = b306_command(channel, args.node, "IMU STOP", "IMU STOP OK ")
            status = b306_command(channel, args.node, "IMU STATUS", "IMU ")
            result["imu_stop"] = {"stop": stop, "status": status}
            if "active=0 " not in f"{status['text']} ":
                raise SessionError(f"IMU did not stop: {status['text']}")
            imu_started = False

            idle_cfg = composed_cfg(args.node, running=False)
            result["idle_cfg"] = relay_command_patient(
                channel, args.node, idle_cfg, "CFG_OK ", attempts=1, reply_timeout_s=85.0
            )
            tag_running = False
            uwb_count = 0
            start = time.monotonic()
            deadline = start + args.idle_witness_s
            while time.monotonic() < deadline:
                line = channel.read(min(deadline, time.monotonic() + 0.5))
                if line and line.startswith("FUSION_UWB ") and parse_fields(line).get("name") == args.node:
                    uwb_count += 1
            result["terminal_idle_witness"] = {
                "duration_s": time.monotonic() - start, "uwb_records": uwb_count,
                "pass": uwb_count <= 1,
            }
            if uwb_count > 1:
                raise SessionError(f"terminal idle witness failed: {uwb_count} UWB records")
            result["status"] = "PASS"
            return 0
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None:
                if imu_started:
                    try:
                        result["emergency_imu_stop"] = b306_command(channel, args.node, "IMU STOP", "IMU STOP OK ")
                    except Exception as exc:
                        result["emergency_imu_stop_error"] = f"{type(exc).__name__}: {exc}"
                if tag_running:
                    try:
                        result["emergency_idle"] = relay_command_patient(
                            channel, args.node, composed_cfg(args.node, running=False),
                            "CFG_OK ", attempts=1, reply_timeout_s=85.0,
                        )
                    except Exception as exc:
                        result["emergency_idle_error"] = f"{type(exc).__name__}: {exc}"
                result["final_host_drain"] = channel.health_snapshot()
                channel.close()
            result["ended"] = now()
            (args.out_dir / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
