#!/usr/bin/env python3
"""Run the decisive five-node v26/v27 comparison for BSF6C53."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from capacity_ramp import (
    BSFS,
    TelemetryAssembler,
    b306_command,
    cleanup,
    collect,
    ensure_imu_stopped,
    relay_command,
    run_one,
    utc_now,
)
from fusion_session import LineChannel, SessionError, resolve_fusion_port
from post_capacity_qualification import strict_link_gate
from pre_ramp_hardening import request_list


TARGET = "BSF6C53"
V26 = "b306-imu-relay-v26"
V27 = "b306-imu-relay-v27"
TAG_MARKER = "tag-fusion-link-v2-relay3"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def expected_b306_marker(node: str, phase: str) -> str:
    if phase == "pre-ota":
        return V27
    return V26 if node == TARGET else V27


def verify_versions(
    channel: LineChannel,
    phase: str,
) -> dict[str, object]:
    result: dict[str, object] = {"b306": {}, "tag": {}}
    for node in BSFS:
        ping = b306_command(channel, node, "PING", "PONG ")
        marker = expected_b306_marker(node, phase)
        if f"fw={marker} " not in f"{ping['text']} ":
            raise SessionError(
                f"{node} B306 marker mismatch: expected {marker}, "
                f"got {ping['text']}"
            )
        result["b306"][node] = ping

        tag = relay_command(channel, node, "VERSION", "VERSION ", attempts=3)
        if f"fw={TAG_MARKER} " not in f"{tag['reply']['text']} ":
            raise SessionError(
                f"{node} tag marker mismatch: {tag['reply']['text']}"
            )
        result["tag"][node] = tag
    return result


def quiet_all(channel: LineChannel) -> dict[str, object]:
    result: dict[str, object] = {"imu": {}, "tag": {}}
    for node in BSFS:
        result["imu"][node] = ensure_imu_stopped(channel, node)
    for node in BSFS:
        result["tag"][node] = relay_command(
            channel, node, "MODE IDLE", "MODE_OK MODE=IDLE", attempts=3
        )
    return result


def wait_strict_link_gate(
    channel: LineChannel,
    assembler: TelemetryAssembler,
    counters: dict[str, int],
    timeout_s: float = 60.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    last_error = "no LIST attempted"
    while time.monotonic() < deadline:
        preflight = request_list(channel, assembler, counters, BSFS)
        try:
            strict_link_gate(preflight)
            return preflight
        except SessionError as exc:
            last_error = str(exc)
    raise SessionError(
        f"strict five-link gate did not settle within {timeout_s:.0f}s: "
        f"{last_error}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("pre-ota", "comparison"), required=True
    )
    parser.add_argument("--duration-s", type=float, default=1800.0)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    if args.phase == "comparison" and args.duration_s < 1799.0:
        raise SessionError("comparison must retain the registered 30-minute window")

    args.output_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": utc_now(),
        "phase": args.phase,
        "expected_nodes": BSFS,
        "target": TARGET,
        "versions": {
            node: expected_b306_marker(node, args.phase) for node in BSFS
        },
        "tag_marker": TAG_MARKER,
        "fixed_conditions": {
            "active_nodes": 5,
            "arm": "C (UWB + IMU)",
            "imu_rate_hz": 200,
            "imu_batch": 5,
            "duration_s": args.duration_s,
            "stop_command": "MODE IDLE",
            "cfg_stop": "FORBIDDEN / NOT USED",
        },
    }
    if args.phase == "comparison":
        summary["prediction_written_before_run"] = {
            "image_regression_hypothesis": (
                "BSF6C53 on archived v26 remains within 199-201 Hz with "
                "zero IMU sequence gaps and no B306-local UWB orphan burst."
            ),
            "falsification": (
                "If BSF6C53 reproduces the early low-rate burst seen on v27, "
                "the image-regression hypothesis is falsified and the next "
                "authorized branch is the time-dependent hardware test."
            ),
            "controls": (
                "The other four nodes remain on v27 in the same five-node "
                "full-load, batch-5, 30-minute run."
            ),
        }
    write_json(args.output_dir / "summary.json", summary)

    channel = None
    try:
        with (args.output_dir / "fusion_raw.log").open(
            "a", buffering=1, encoding="utf-8"
        ) as raw:
            channel = LineChannel(
                resolve_fusion_port(args.fusion_port), raw, "FUSION"
            )
            assembler = TelemetryAssembler()
            counters: dict[str, int] = {}
            collect(channel, assembler, 2.0)
            preflight = wait_strict_link_gate(
                channel, assembler, counters
            )
            summary["strict_link_gate"] = preflight
            summary["version_gate"] = verify_versions(channel, args.phase)

            if args.phase == "pre-ota":
                summary["quiet_state"] = quiet_all(channel)
            else:
                result = run_one(
                    args.output_dir,
                    channel,
                    5,
                    "C",
                    args.duration_s,
                    126,
                    {
                        "p95_us": 97_600.0,
                        "max_us": 207_400.0,
                    },
                    "N5_C_batch5_BSF6C53_v26",
                    imu_batch=5,
                )
                c53 = result["per_node"][TARGET]
                summary["comparison"] = {
                    "run_pass": result["pass"],
                    "gates": result["gates"],
                    "aggregate": result["aggregate"],
                    "per_node": result["per_node"],
                    "image_regression_prediction_met": (
                        199.0 <= c53["imu_effective_rate_hz"] <= 201.0
                        and c53["imu_sequence_gaps"] == 0
                        and c53["hard_anomaly_deltas"].get(
                            "orphan_frame", 0
                        ) == 0
                    ),
                }
                summary["cleanup"] = cleanup(channel)
            summary["status"] = "COMPLETE"
            summary["completed_utc"] = utc_now()
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        summary["completed_utc"] = utc_now()
        if channel is not None:
            try:
                with (args.output_dir / "emergency_cleanup.log").open(
                    "a", buffering=1, encoding="utf-8"
                ) as emergency:
                    channel.log_file = emergency
                    summary["cleanup"] = cleanup(channel)
            except Exception as cleanup_exc:
                summary["cleanup_error"] = str(cleanup_exc)
        raise
    finally:
        if channel is not None:
            channel.close()
        write_json(args.output_dir / "summary.json", summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SessionError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
