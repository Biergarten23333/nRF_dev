#!/usr/bin/env python3
"""Two-minute, one-board batch-10 sanity check for the v32 OTA batch.

This intentionally leaves the tag/UWB configuration untouched.  It proves
only the amendment's application-plane gates: exact identity and confirmed
v32, IMU batch echo, N=10 records flowing, and zero q_drop_imu growth.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from capacity_ramp import RecordingAssembler, b306_command, collect
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import FusionController, SessionError, imu_sequence_gaps, parse_fields, resolve_fusion_port, u32_delta
from pre_ramp_hardening import request_list
from v32_service_gate import require_production_regime


MASTER_MARKER = "dk-fusion-imu-relay-v28"
B306_MARKER = "b306-imu-relay-v32"
REMAINING_NINE = (
    "BSF3C79",
    "BSFC2CC",
    "BSF44AD",
    "BSF6C53",
    "BSF1120",
    "BSF31CC",
    "BSFAA61",
    "BSFEC35",
    "BSFB165",
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def wait_queue(channel: ThreadedLineChannel, node: str, timeout_s: float = 8.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line and line.startswith("FUSION_QUEUE "):
            fields = parse_fields(line)
            if fields.get("name") == node:
                return fields
    raise SessionError(f"fresh queue counters missing for {node}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True, choices=REMAINING_NINE)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--duration-s", type=float, default=120.0)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    if args.duration_s < 110.0:
        raise SystemExit("duration must be at least 110 s for the ~2 min gate")
    args.out_dir.mkdir(parents=True, exist_ok=False)

    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": now(),
        "node": args.node,
        "expected_online_nodes": list(REMAINING_NINE),
        "duration_requested_s": args.duration_s,
        "uwb_configuration_policy": "observe-only; no TAG/UWB command is sent",
    }
    channel: ThreadedLineChannel | None = None
    imu_started = False
    with (args.out_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(args.fusion_port),
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

            master = wait_master_status(channel)
            result["master_status"] = master
            if f"marker={MASTER_MARKER}" not in master:
                raise SessionError(f"master marker mismatch: {master}")
            master_fields = parse_fields(master)
            master_generation = int(
                master_fields.get("spacing_generation", "-1"), 0
            )
            require_production_regime(
                spacing=master_fields.get("spacing", ""),
                spacing_us=int(master_fields.get("spacing_us", "-1"), 0),
                spacing_generation=master_generation,
                current_generation=master_generation,
            )

            assembler = RecordingAssembler()
            collect(channel, assembler, 1.0)
            listing = request_list(channel, assembler, {}, REMAINING_NINE)
            result["list"] = listing
            aggregate = listing["aggregate"]
            list_generation = int(
                aggregate.get("spacing_generation", "-1"), 0
            )
            require_production_regime(
                spacing=aggregate.get("spacing", ""),
                spacing_us=int(aggregate.get("spacing_us", "-1"), 0),
                spacing_generation=list_generation,
                current_generation=master_generation,
            )
            result["production_spacing_gate"] = {
                "spacing": aggregate.get("spacing"),
                "spacing_us": aggregate.get("spacing_us"),
                "spacing_generation": list_generation,
                "master_generation": master_generation,
                "verdict": "PASS",
            }
            ready = {
                node
                for node, row in listing["peers"].items()
                if row.get("connected") == "1"
                and row.get("subscribed") == "1"
                and row.get("link_contract") == "PASS"
            }
            if set(listing["peers"]) != set(REMAINING_NINE) or ready != set(REMAINING_NINE):
                raise SessionError(
                    f"remaining-nine peer gate failed: peers={sorted(listing['peers'])} "
                    f"ready={sorted(ready)}"
                )

            ping = b306_command(channel, args.node, "PING", "PONG ")
            confirm = b306_command(channel, args.node, "BOOT CONFIRM STATUS", "BOOT CONFIRM STATUS ")
            result["identity"] = {"ping": ping, "confirm": confirm}
            ping_text = str(ping["text"])
            confirm_text = str(confirm["text"])
            if f"name={args.node}" not in ping_text or f"fw={B306_MARKER}" not in ping_text:
                raise SessionError(f"identity/marker mismatch: {ping_text}")
            if "confirmed=1" not in confirm_text:
                raise SessionError(f"v32 is not confirmed: {confirm_text}")

            pre_status = b306_command(channel, args.node, "IMU STATUS", "IMU ")
            pre_stop = None
            pre_stop_telemetry = None
            if "active=1 " in f"{pre_status['text']} ":
                controller = FusionController(channel, args.node, timeout_s=8.0, max_attempts=1)
                stop_reply = controller.command(
                    "IMU STOP",
                    lambda text: (
                        text.startswith("IMU STOP OK ")
                        or text.startswith("IMU STOP FAIL err=-120 ")
                    ),
                    allow_resend_after_tx=False,
                )
                pre_stop = stop_reply.__dict__
                if stop_reply.text.startswith("IMU STOP FAIL err=-120 "):
                    # Accept the already-stopped race only with fresh device
                    # telemetry, exactly as required by the batch amendment.
                    pre_stop_telemetry = controller.wait_telemetry()
                    if pre_stop_telemetry.get("imu_active") != "0":
                        raise SessionError(
                            "IMU STOP err=-120 without fresh imu_active=0 proof: "
                            f"{pre_stop_telemetry}"
                        )
                post_stop_status = b306_command(channel, args.node, "IMU STATUS", "IMU ")
            elif "active=0 " in f"{pre_status['text']} ":
                post_stop_status = pre_status
            else:
                raise SessionError(f"IMU STATUS missing active state: {pre_status['text']}")
            result["imu_preflight"] = {
                "status_before": pre_status,
                "stop": pre_stop,
                "err_minus_120_telemetry": pre_stop_telemetry,
                "status_after": post_stop_status,
            }
            if "active=0 " not in f"{post_stop_status['text']} ":
                raise SessionError(f"IMU active after preflight: {post_stop_status['text']}")

            rate_reply = b306_command(channel, args.node, "IMU RATE=200", "IMU RATE OK ")
            batch_reply = b306_command(channel, args.node, "IMU BATCH=10", "IMU BATCH OK ")
            start_reply = b306_command(channel, args.node, "IMU START", "IMU START OK ")
            imu_started = True
            result["imu_commands"] = {
                "rate": rate_reply,
                "batch": batch_reply,
                "start": start_reply,
            }
            if "n=10" not in str(batch_reply["text"]):
                raise SessionError(f"batch=10 echo missing: {batch_reply['text']}")
            if any(token not in str(start_reply["text"]) for token in (
                "61=0001:P", "03=000B:P", "1F=0002:P", "volatile=1", "saved=0",
            )):
                raise SessionError(f"IMU START verification failed: {start_reply['text']}")

            baseline = wait_queue(channel, args.node)
            result["queue_before"] = baseline
            boundary = channel.discard_pending(f"{args.node}_batch10_sanity_start")
            decoder_before = channel.binary_decoder.errors
            started = time.monotonic()
            deadline = started + args.duration_s
            lines: list[str] = []
            while time.monotonic() < deadline:
                line = channel.read(min(deadline, time.monotonic() + 0.5))
                if line is not None:
                    lines.append(line)
            ended = time.monotonic()
            final = wait_queue(channel, args.node)
            result["queue_after"] = final

            imu_lines = [
                line for line in lines
                if line.startswith("FUSION_IMU ") and parse_fields(line).get("name") == args.node
            ]
            uwb_lines = [
                line for line in lines
                if line.startswith("FUSION_UWB ") and parse_fields(line).get("name") == args.node
            ]
            n_values = Counter(parse_fields(line).get("n") for line in imu_lines)
            imu_samples = sum(int(parse_fields(line).get("n", "0"), 0) for line in imu_lines)
            seq_gaps, _ = imu_sequence_gaps(imu_lines)
            duration = ended - started
            q_delta = u32_delta(int(baseline["q_drop_imu"], 0), int(final["q_drop_imu"], 0))
            decoder_delta = channel.binary_decoder.errors - decoder_before
            formal_pass = q_delta == 0 and int(n_values.get("10", 0)) > 0 and len(imu_lines) > 0
            result["formal"] = {
                "boundary": boundary,
                "duration_s": duration,
                "imu_records": len(imu_lines),
                "imu_n10_records": int(n_values.get("10", 0)),
                "imu_n_values": dict(n_values),
                "imu_samples": imu_samples,
                "imu_sample_rate_hz": imu_samples / duration,
                "imu_sequence_gaps_report_only": seq_gaps,
                "uwb_records_observed_report_only": len(uwb_lines),
                "q_drop_imu_delta": q_delta,
                "decoder_error_delta_report_only": decoder_delta,
                "host_drain_report_only": channel.health_snapshot(),
                "specified_gate_pass": formal_pass,
            }
            if not formal_pass:
                raise SessionError(f"batch-10 sanity gates failed: {result['formal']}")

            stop = b306_command(channel, args.node, "IMU STOP", "IMU STOP OK ")
            status = b306_command(channel, args.node, "IMU STATUS", "IMU ")
            result["imu_terminal"] = {"stop": stop, "status": status}
            if "active=0 " not in f"{status['text']} ":
                raise SessionError(f"IMU did not stop: {status['text']}")
            imu_started = False
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
                        result["emergency_imu_stop"] = b306_command(
                            channel, args.node, "IMU STOP", "IMU STOP OK "
                        )
                    except Exception as exc:
                        result["emergency_imu_stop_error"] = f"{type(exc).__name__}: {exc}"
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
