#!/usr/bin/env python3
"""Batch-G day-run H1: isolate and behavior-check BSFAA61."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from batch_g_overnight import NODES, Runner, active_cfg, b306_command
from fusion_session import SessionError


TARGET = "BSFAA61"
TARGET_TAG = 8
MID_SLOT = 5


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()

    runner = Runner(args.evidence_root, args.fusion_port, 1.0)
    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "target": TARGET,
        "tag": TARGET_TAG,
        "mid_slot": MID_SLOT,
        "temporary_period_sequence_ms": [100, 110, 100],
    }
    try:
        runner.open()
        listing = runner.list_peers()
        aggregate = listing["aggregate"]
        if (
            aggregate.get("count") != "10"
            or aggregate.get("ready") != "10"
            or set(listing["peers"]) != set(NODES)
        ):
            raise SessionError(f"H1 requires 10/10 peers: {listing}")
        result["list_gate"] = listing

        assert runner.channel is not None
        imu_status = b306_command(
            runner.channel, TARGET, "IMU STATUS", "IMU "
        )
        result["imu_status_before"] = imu_status
        if "active=1 " in f"{imu_status['text']} ":
            result["imu_stop"] = runner.stop_imu(TARGET)
            if not result["imu_stop"]["ok"]:
                raise SessionError(f"{TARGET} IMU failed to stop")
        else:
            result["imu_stop"] = {
                "needed": False,
                "reason": "independent IMU STATUS reported active=0",
            }

        cfg = active_cfg(TARGET_TAG, MID_SLOT, count=11, beacon_win_n=1)
        result["cfg_command"] = cfg
        result["cfg_result"] = runner.send_tag_cfg(TARGET, cfg)
        result["period110"] = runner.set_main_period(110, "h1")
        runner.period_us = 110_000
        result["window"] = runner.measured_window("h1_bsfaa61", 120.0)
        row = result["window"]["nodes"].get(TARGET, {})
        completion = result["cfg_result"].get("completion")
        branch_a = bool(
            completion == "cfg_ok"
            and row.get("available")
            and row.get("sweep_delta", 0) > 0
            and row.get("lock_before") == "1"
            and row.get("lock_after") == "1"
            and row.get("gen_before") == row.get("gen_after")
        )
        result["branch"] = "A" if branch_a else "B"
        result["branch_reason"] = {
            "cfg_completion": completion,
            "available": row.get("available"),
            "sweep_delta": row.get("sweep_delta"),
            "tag_domain_rate_hz": row.get("tag_domain_rate_hz"),
            "lock_before": row.get("lock_before"),
            "lock_after": row.get("lock_after"),
            "gen_before": row.get("gen_before"),
            "gen_after": row.get("gen_after"),
            "window_miss_fraction": row.get("window_miss_fraction"),
        }
        result["period100_restore"] = runner.set_main_period(100, "h1_restore")
        runner.period_us = 100_000

        if branch_a:
            result["status"] = "PASS_BRANCH_A"
        else:
            idle = (
                f"CFG TAG={TARGET_TAG} SLOT={MID_SLOT} COUNT=11 PERIOD=10 "
                "ACTIVE=9 EPOCH=5000 BEACON_SYNC=0 BEACON_WIN_N=1 "
                "DW_ANCHOR=0 RUN=0 PMODE=3"
            )
            result["quarantine_idle_cfg"] = runner.send_tag_cfg(TARGET, idle)
            witness = runner.capture("h1_quarantine_idle_witness", 90.0)
            result["quarantine_witness"] = witness
            result["quarantine_zero_uwb"] = (
                witness["records"].get(TARGET, 0) <= 1
            )
            if not result["quarantine_zero_uwb"]:
                raise SessionError(
                    f"{TARGET} composed-IDLE zero-UWB witness failed"
                )
            result["status"] = "PASS_BRANCH_B_QUARANTINED"
        return 0
    except Exception as exc:
        result["status"] = "FAILED"
        result["error"] = f"{type(exc).__name__}: {exc}"
        try:
            if runner.period_us != 100_000:
                result["exception_period100_restore"] = runner.set_main_period(
                    100, "h1_exception_restore"
                )
                runner.period_us = 100_000
        except Exception as restore_exc:
            result["restore_error"] = (
                f"{type(restore_exc).__name__}: {restore_exc}"
            )
        return 2
    finally:
        result["ended_monotonic"] = time.monotonic()
        args.evidence_root.mkdir(parents=True, exist_ok=True)
        write_json(args.evidence_root / "H1_RESULT.json", result)
        runner.checkpoint()
        if runner.channel is not None:
            runner.channel.close()
        if runner.raw is not None:
            runner.raw.close()
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
