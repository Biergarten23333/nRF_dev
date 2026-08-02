#!/usr/bin/env python3
"""Pre-registered N5-C batch-size lever after the primary capacity cliff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from capacity_ramp import (
    BSFS,
    TelemetryAssembler,
    cleanup,
    collect,
    run_one,
    utc_now,
)
from fusion_session import LineChannel, SessionError, resolve_fusion_port
from pre_ramp_hardening import request_list


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=300.0)
    parser.add_argument("--imu-batch", type=int, default=5)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    if args.imu_batch <= 2:
        raise SessionError("lever batch must be larger than the primary N=2")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": utc_now(),
        "test": "N5-C notification-count lever",
        "imu_batch": args.imu_batch,
        "primary_reference": (
            "B306_Part/logs/capacity_ramp_20260727/"
            "formal_ramp_v21_r5/N5_C/analysis.json"
        ),
        "prediction": (
            "If the knee is notification-count limited, batch=5 reduces "
            "IMU notifies from about 500/s to 200/s aggregate and removes "
            "logger/CDC drops and sequence gaps."
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
            preflight = request_list(channel, assembler, counters, BSFS)
            if (
                preflight["aggregate"].get("count") != "5"
                or preflight["aggregate"].get("ready") != "5"
            ):
                raise SessionError(
                    f"five-link preflight failed: {preflight}"
                )
            summary["preflight"] = preflight
            result = run_one(
                args.output_dir,
                channel,
                5,
                "C",
                args.duration_s,
                70,
                None,
                f"N5_C_batch{args.imu_batch}",
                imu_batch=args.imu_batch,
            )
            summary["result"] = {
                "pass": result["pass"],
                "gates": result["gates"],
                "delivered_notifications_s": result["aggregate"][
                    "delivered_notifications_s"
                ],
                "latency": result["aggregate"]["latency"],
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
    except (SessionError, OSError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
