#!/usr/bin/env python3
"""Measure the clean binary-egress lower bound with five IMU-only nodes."""

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
    parser.add_argument("--duration-s", type=float, default=120.0)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": utc_now(),
        "prediction": (
            "Binary egress remains lossless beyond the old 400-462 notify/s "
            "ASCII cliff. Source missed-deadline telemetry separates a B306 "
            "production limit from a DK logger/CDC limit."
        ),
        "batches": (5, 4, 3, 2, 1),
        "results": {},
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
                or set(preflight["peers"]) != set(BSFS)
            ):
                raise SessionError(f"five-link preflight failed: {preflight}")
            summary["preflight"] = preflight

            for batch in (5, 4, 3, 2, 1):
                label = f"N5_B_batch{batch}"
                result = run_one(
                    args.output_dir,
                    channel,
                    5,
                    "B",
                    args.duration_s,
                    100 + batch,
                    None,
                    label,
                    imu_batch=batch,
                )
                master_clean = (
                    result["gates"]["zero_logger_drop"]
                    and result["gates"]["zero_cdc_drop"]
                    and result["gates"]["zero_malformed"]
                    and result["gates"]["zero_disconnects"]
                )
                missed = {
                    node: result["per_node"][node][
                        "hard_anomaly_deltas"
                    ].get("imu_missed_deadlines", 0)
                    for node in BSFS
                }
                summary["results"][label] = {
                    "master_egress_clean": master_clean,
                    "delivered_notifications_s": result["aggregate"][
                        "delivered_notifications_s"
                    ],
                    "expected_notifications_s": result["aggregate"][
                        "expected_notifications_s"
                    ],
                    "source_missed_deadlines": missed,
                    "gates": result["gates"],
                }
                write_json(args.output_dir / "summary.json", summary)
                if not master_clean:
                    break

            clean_rates = [
                row["delivered_notifications_s"]
                for row in summary["results"].values()
                if row["master_egress_clean"]
            ]
            summary["clean_binary_egress_lower_bound_notifications_s"] = (
                max(clean_rates) if clean_rates else 0.0
            )
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
                ) as emergency_log:
                    channel.log_file = emergency_log
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
