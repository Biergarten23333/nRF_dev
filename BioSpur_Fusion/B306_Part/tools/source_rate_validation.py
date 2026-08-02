#!/usr/bin/env python3
"""Isolate B306 IMU source rate at batch 2 versus 5 on two physical units."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from capacity_ramp import BSFS, RecordingAssembler, collect, relay_command
from fusion_session import (
    FusionController,
    LineChannel,
    SessionError,
    imu_sequence_gaps,
    parse_fields,
    resolve_fusion_port,
    u32_delta,
)
from pre_ramp_hardening import request_list


TARGETS = ("BSF3C79", "BSFC2CC")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def command(channel: LineChannel, bsf: str, text: str, prefix: str) -> None:
    FusionController(channel, bsf, 8.0, 3).command(
        text,
        lambda reply: reply.startswith(prefix),
        allow_resend_after_tx=False,
    )


def telemetry_rows(lines: list[str], bsf: str) -> list[dict[str, str]]:
    return [
        parse_fields(line)
        for line in lines
        if line.startswith("FUSION_TELEMETRY ") and f"name={bsf} " in line
    ]


def analyze(lines: list[str], bsf: str, duration_s: float) -> dict[str, object]:
    imu_lines = [
        line
        for line in lines
        if line.startswith("FUSION_IMU ") and f"name={bsf} " in line
    ]
    samples = 0
    first_us: int | None = None
    last_us: int | None = None
    for line in imu_lines:
        fields = parse_fields(line)
        count = int(fields["n"], 0)
        base = int(fields["base_us"], 0)
        final_delta = int(fields["samples"].split(";")[-1].split(",")[0])
        samples += count
        first_us = base if first_us is None else min(first_us, base)
        last_us = base + final_delta if last_us is None else max(
            last_us, base + final_delta
        )
    span_s = (
        (last_us - first_us) / 1_000_000.0
        if first_us is not None and last_us is not None and last_us > first_us
        else duration_s
    )
    rows = telemetry_rows(lines, bsf)
    missed_delta = (
        u32_delta(
            int(rows[0].get("imu_missed_deadlines", "0"), 0),
            int(rows[-1].get("imu_missed_deadlines", "0"), 0),
        )
        if len(rows) >= 2
        else None
    )
    gaps, records = imu_sequence_gaps(imu_lines)
    return {
        "bsf": bsf,
        "records": records,
        "samples": samples,
        "timer_span_s": span_s,
        "effective_rate_hz": samples / span_s if span_s > 0 else 0.0,
        "sequence_gaps": gaps,
        "telemetry_records": len(rows),
        "missed_deadlines_delta": missed_delta,
        "transport_counter_deltas": (
            {
                name: u32_delta(
                    int(rows[0].get(name, "0"), 0),
                    int(rows[-1].get(name, "0"), 0),
                )
                for name in (
                    "drop_err", "malformed", "logger_drop",
                    "imu_i2c_err",
                )
            }
            if len(rows) >= 2
            else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=300.0)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "targets": TARGETS,
        "batches": (2, 5),
        "duration_s": args.duration_s,
        "prediction": (
            "BSF3C79 loses source samples at batch 2, BSFC2CC varies by unit, "
            "and batch 5 restores both near 200 Hz; every lost period appears "
            "in imu_missed_deadlines."
        ),
        "results": [],
    }
    channel = None
    try:
        with (args.output_dir / "raw.log").open(
            "a", encoding="utf-8", buffering=1
        ) as raw:
            channel = LineChannel(
                resolve_fusion_port(args.fusion_port), raw, "FUSION"
            )
            assembler = RecordingAssembler()
            counters: dict[str, int] = {}
            collect(channel, assembler, 2.0)
            preflight = request_list(channel, assembler, counters, BSFS)
            if preflight["aggregate"].get("ready") != "5":
                raise SessionError(f"five-link preflight failed: {preflight}")
            summary["preflight"] = preflight

            for bsf in BSFS:
                try:
                    command(channel, bsf, "IMU STOP", "IMU STOP ")
                except SessionError:
                    pass
                relay_command(
                    channel, bsf, "MODE IDLE", "MODE_OK MODE=IDLE", 3
                )

            for bsf in TARGETS:
                for batch in (2, 5):
                    print(
                        f"SOURCE_RATE_START bsf={bsf} batch={batch} "
                        f"duration_s={args.duration_s}",
                        flush=True,
                    )
                    command(channel, bsf, "COUNTERS CLEAR", "COUNTERS CLEARED")
                    command(channel, bsf, f"IMU BATCH={batch}", "IMU BATCH OK")
                    command(channel, bsf, "IMU START", "IMU START ")
                    rows = collect(
                        channel, RecordingAssembler(), args.duration_s,
                        retain=True,
                    )
                    command(channel, bsf, "IMU STOP", "IMU STOP ")
                    lines = [line for _, line in rows]
                    result = analyze(lines, bsf, args.duration_s)
                    result["batch"] = batch
                    summary["results"].append(result)
                    write_json(args.output_dir / "summary.json", summary)
                    print(
                        f"SOURCE_RATE_END bsf={bsf} batch={batch} "
                        f"rate_hz={result['effective_rate_hz']:.6f} "
                        f"missed={result['missed_deadlines_delta']} "
                        f"gaps={result['sequence_gaps']}",
                        flush=True,
                    )
                    time.sleep(1.0)
            summary["status"] = "COMPLETE"
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        raise
    finally:
        if channel is not None:
            for bsf in BSFS:
                try:
                    command(channel, bsf, "IMU STOP", "IMU STOP ")
                except Exception:
                    pass
            channel.close()
        write_json(args.output_dir / "summary.json", summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SessionError, OSError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
