#!/usr/bin/env python3
"""Capture the v29 IMU pull and publish diagnostics."""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import RecordingAssembler, collect
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


HIST_RE = re.compile(
    r"IMU PULL HIST kind=(?P<kind>[LD]) p=(?P<page>\d+) "
    r"first=(?P<first>\d+) n=(?P<count>\d+) h=(?P<hist>[\d,]+)"
)
EP_RE = re.compile(
    r"IMU PULL EP p=(?P<page>\d+) first=(?P<first>\d+) "
    r"n=(?P<count>\d+) total=(?P<total>\d+) drop=(?P<drop>\d+) "
    r"e=(?P<episodes>.*)"
)
PUB_HIST_RE = re.compile(
    r"IMU PUB HIST p=(?P<page>\d+) first=(?P<first>\d+) "
    r"n=(?P<count>\d+) h=(?P<hist>[\d,]+)"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def command(
    controller: FusionController, text: str, expected_prefix: str
) -> dict[str, object]:
    reply = controller.command(
        text,
        lambda value: value.startswith(expected_prefix),
        allow_resend_after_tx=False,
    )
    return dict(reply.__dict__)


def telemetry_rows(lines: list[str], bsf: str) -> list[dict[str, str]]:
    return [
        parse_fields(line)
        for line in lines
        if line.startswith("FUSION_TELEMETRY ") and f"name={bsf} " in line
    ]


def analyze_stream(
    lines: list[str], bsf: str, duration_s: float
) -> dict[str, object]:
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
        last = base + final_delta
        last_us = last if last_us is None else max(last_us, last)
    timer_span_s = (
        (last_us - first_us) / 1_000_000.0
        if first_us is not None and last_us is not None and last_us > first_us
        else duration_s
    )
    telemetry = telemetry_rows(lines, bsf)
    gaps, records = imu_sequence_gaps(imu_lines)
    counter_fields = (
        "imu_missed_deadlines",
        "imu_i2c_err",
        "drop_err",
        "malformed",
        "logger_drop",
    )
    deltas = None
    if len(telemetry) >= 2:
        deltas = {
            field: u32_delta(
                int(telemetry[0].get(field, "0"), 0),
                int(telemetry[-1].get(field, "0"), 0),
            )
            for field in counter_fields
        }
    return {
        "records": records,
        "samples": samples,
        "timer_span_s": timer_span_s,
        "effective_rate_hz": samples / timer_span_s if timer_span_s > 0 else 0.0,
        "sequence_gaps": gaps,
        "telemetry_records": len(telemetry),
        "telemetry_first": telemetry[0] if telemetry else None,
        "telemetry_last": telemetry[-1] if telemetry else None,
        "counter_deltas": deltas,
    }


def collect_window(
    channel: LineChannel, duration_s: float
) -> list[tuple[float, str]]:
    rows: list[tuple[float, str]] = []
    start = time.monotonic()
    next_progress = start + 30.0
    end = start + duration_s
    assembler = RecordingAssembler()
    while time.monotonic() < end:
        deadline = min(end, next_progress)
        rows.extend(
            collect(
                channel,
                assembler,
                max(0.0, deadline - time.monotonic()),
                retain=True,
            )
        )
        now = time.monotonic()
        if now >= next_progress and now < end:
            print(
                f"CAPTURE_PROGRESS elapsed_s={now - start:.1f} "
                f"remaining_s={end - now:.1f}",
                flush=True,
            )
            next_progress += 30.0
    return rows


def parse_hist(text: str) -> dict[str, object]:
    match = HIST_RE.fullmatch(text)
    if match is None:
        raise SessionError(f"unparseable pull histogram: {text}")
    item: dict[str, object] = {
        "kind": match["kind"],
        "page": int(match["page"]),
        "first": int(match["first"]),
        "count": int(match["count"]),
        "hist": [int(value) for value in match["hist"].split(",")],
    }
    if len(item["hist"]) != item["count"]:
        raise SessionError(f"short pull histogram: {text}")
    return item


def parse_episode_page(text: str) -> dict[str, object]:
    match = EP_RE.fullmatch(text)
    if match is None:
        raise SessionError(f"unparseable pull episode page: {text}")
    episodes: list[dict[str, int]] = []
    encoded = match["episodes"]
    if encoded:
        for entry in encoded.split(","):
            (
                deadline,
                lateness,
                misses,
                recovery,
                publish_duration,
                publish_overlap,
            ) = entry.split(":")
            episodes.append(
                {
                    "first_deadline_low_us": int(deadline, 16),
                    "first_lateness_us": int(lateness, 16),
                    "consecutive_misses": int(misses, 16),
                    "recovery_lateness_us": int(recovery, 16),
                    "publish_duration_us": int(publish_duration, 16),
                    "publish_overlap": int(publish_overlap, 16),
                }
            )
    if len(episodes) != int(match["count"]):
        raise SessionError(f"short pull episode page: {text}")
    return {
        "page": int(match["page"]),
        "first": int(match["first"]),
        "count": int(match["count"]),
        "total": int(match["total"]),
        "drop": int(match["drop"]),
        "episodes": episodes,
    }


def parse_publish_hist(text: str) -> dict[str, object]:
    match = PUB_HIST_RE.fullmatch(text)
    if match is None:
        raise SessionError(f"unparseable publish histogram: {text}")
    histogram = [int(value) for value in match["hist"].split(",")]
    if len(histogram) != int(match["count"]):
        raise SessionError(f"short publish histogram: {text}")
    return {
        "page": int(match["page"]),
        "first": int(match["first"]),
        "count": int(match["count"]),
        "hist": histogram,
    }


def query_diagnostics(
    controller: FusionController, output_dir: Path, batch: int
) -> dict[str, object]:
    pull = command(controller, "IMU PULL", "IMU PULL ")
    pull_fields = parse_fields(str(pull["text"]))
    histograms: dict[str, list[int]] = {}
    hist_pages: dict[str, list[dict[str, object]]] = {}
    for kind, name in (("LAT", "lateness"), ("DUR", "duration")):
        pages = [
            parse_hist(
                str(command(
                    controller,
                    f"IMU PULL {kind}={page}",
                    "IMU PULL HIST ",
                )["text"])
            )
            for page in range(4)
        ]
        hist_pages[name] = pages
        histograms[name] = [
            value for page in pages for value in page["hist"]
        ]

    total = int(pull_fields["ep"], 0)
    page_count = math.ceil(total / 4)
    episode_pages: list[dict[str, object]] = []
    episodes: list[dict[str, int]] = []
    for page in range(page_count):
        parsed = parse_episode_page(
            str(command(
                controller,
                f"IMU PULL EP={page}",
                "IMU PULL EP ",
            )["text"])
        )
        episode_pages.append(parsed)
        episodes.extend(parsed["episodes"])
        if (page + 1) % 100 == 0:
            print(
                f"EPISODE_DOWNLOAD batch={batch} pages={page + 1}/"
                f"{page_count}",
                flush=True,
            )

    episode_file = output_dir / f"batch{batch}_episodes.json"
    write_json(episode_file, episodes)
    publish = command(controller, "IMU PUB", "IMU PUB ")
    publish_fields = parse_fields(str(publish["text"]))
    publish_pages = [
        parse_publish_hist(
            str(command(
                controller,
                f"IMU PUB HIST={page}",
                "IMU PUB HIST ",
            )["text"])
        )
        for page in range(4)
    ]
    return {
        "summary_reply": pull,
        "summary_fields": pull_fields,
        "histogram_pages": hist_pages,
        "histograms": histograms,
        "episode_total": total,
        "episode_drop": int(pull_fields["drop"], 0),
        "episode_pages": len(episode_pages),
        "episode_file": str(episode_file),
        "episodes": episodes,
        "publish_summary_reply": publish,
        "publish_summary_fields": publish_fields,
        "publish_histogram_pages": publish_pages,
        "publish_histogram": [
            value for page in publish_pages for value in page["hist"]
        ],
        "delta_pages": [
            command(controller, f"IMU DELTA={page}", f"IMU DELTA p={page} ")
            for page in range(3)
        ],
        "status": command(controller, "IMU STATUS", "IMU "),
        "counters": command(controller, "COUNTERS", "CTR2 "),
    }


def run_arm(
    channel: LineChannel,
    controller: FusionController,
    output_dir: Path,
    bsf: str,
    batch: int,
    duration_s: float,
) -> dict[str, object]:
    print(
        f"ARM_START batch={batch} duration_s={duration_s} utc={utc_now()}",
        flush=True,
    )
    preflight = {
        "clear": command(controller, "COUNTERS CLEAR", "COUNTERS CLEARED"),
        "batch": command(controller, f"IMU BATCH={batch}", "IMU BATCH OK "),
        "start": command(controller, "IMU START", "IMU START OK "),
    }
    formal_start = utc_now()
    rows = collect_window(channel, duration_s)
    formal_end = utc_now()
    stopped = command(controller, "IMU STOP", "IMU STOP OK ")
    lines = [line for _, line in rows]
    stream = analyze_stream(lines, bsf, duration_s)
    diagnostics = query_diagnostics(controller, output_dir, batch)
    result = {
        "batch": batch,
        "duration_s": duration_s,
        "formal_start_utc": formal_start,
        "formal_end_utc": formal_end,
        "preflight": preflight,
        "stop": stopped,
        "stream": stream,
        "diagnostics": diagnostics,
    }
    write_json(output_dir / f"batch{batch}.json", result)
    print(
        f"ARM_COMPLETE batch={batch} rate_hz="
        f"{stream['effective_rate_hz']:.6f} gaps={stream['sequence_gaps']} "
        f"episodes={diagnostics['episode_total']} "
        f"late_max_us={diagnostics['summary_fields']['lm']} "
        f"dur_max_us={diagnostics['summary_fields']['dm']}",
        flush=True,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--bsf", default="BSF3C79")
    parser.add_argument("--duration-s", type=float, default=600.0)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "created_utc": utc_now(),
        "bsf": args.bsf,
        "duration_s": args.duration_s,
        "batches": [2, 5],
        "verdict_rules": [
            "one tens-of-ms first lateness plus normal duration => BLOCKED",
            "repeated approximately 5-ms lateness plus normal duration => periodic blocker",
            "duration grows and lateness accumulates => SLOWED",
            "normal lateness and duration despite misses => neither model",
        ],
        "results": [],
    }
    channel: LineChannel | None = None
    controller: FusionController | None = None
    try:
        with (args.output_dir / "raw.log").open(
            "a", encoding="utf-8", buffering=1
        ) as raw:
            channel = LineChannel(
                resolve_fusion_port(args.fusion_port), raw, "FUSION"
            )
            channel.send("OUTPUT BINARY")
            collect(channel, RecordingAssembler(), 2.0)
            preflight = request_list(
                channel, RecordingAssembler(), {}, (args.bsf,)
            )
            if preflight["aggregate"].get("ready") != "1":
                raise SessionError(f"single-node preflight failed: {preflight}")
            if tuple(preflight["peers"]) != (args.bsf,):
                raise SessionError(f"wrong peer set: {preflight}")
            summary["preflight"] = preflight
            controller = FusionController(channel, args.bsf, 8.0, 3)
            ping = command(controller, "PING", "PONG ")
            if "fw=b306-imu-relay-v29 " not in f"{ping['text']} ":
                raise SessionError(f"wrong B306 image: {ping}")
            summary["ping"] = ping
            status = command(controller, "IMU STATUS", "IMU ")
            if "active=0 " not in f"{status['text']} ":
                command(controller, "IMU STOP", "IMU STOP OK ")

            for batch in (2, 5):
                result = run_arm(
                    channel,
                    controller,
                    args.output_dir,
                    args.bsf,
                    batch,
                    args.duration_s,
                )
                summary["results"].append(result)
                write_json(args.output_dir / "summary.json", summary)
            summary["status"] = "COMPLETE"
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        raise
    finally:
        if controller is not None:
            try:
                status = command(controller, "IMU STATUS", "IMU ")
                if "active=1 " in f"{status['text']} ":
                    command(controller, "IMU STOP", "IMU STOP OK ")
                command(controller, "IMU BATCH=5", "IMU BATCH OK ")
            except Exception as exc:
                summary["cleanup_error"] = str(exc)
        if channel is not None:
            channel.close()
        write_json(args.output_dir / "summary.json", summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
