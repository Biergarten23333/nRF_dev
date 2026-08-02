#!/usr/bin/env python3
"""Measure whether B306 IMU loss episodes occur inside GATT notify calls."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import (
    BSFS,
    RecordingAssembler,
    b306_command,
    cleanup,
    collect,
    relay_command,
    run_one,
)
from fusion_session import (
    FusionController,
    LineChannel,
    SessionError,
    resolve_fusion_port,
)
from measure_imu_pull import query_diagnostics
from post_capacity_qualification import strict_link_gate
from pre_ramp_hardening import request_list


EXPECTED_B306_MARKER = "b306-imu-relay-v29"
EXPECTED_TAG_MARKER = "tag-fusion-link-v2-relay3"
CONNECTION_INTERVAL_US = 50_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (
        ordered[upper] - ordered[lower]
    ) * (position - lower)


def unwrap_low_words(values: list[int]) -> list[int]:
    if not values:
        return []
    result = [values[0]]
    high = 0
    previous = values[0]
    for value in values[1:]:
        if value < previous and previous - value > 0x80000000:
            high += 1 << 32
        result.append(high + value)
        previous = value
    return result


def minimum_circular_span(phases: list[int], fraction: float) -> int | None:
    if not phases:
        return None
    ordered = sorted(value % CONNECTION_INTERVAL_US for value in phases)
    count = max(1, math.ceil(len(ordered) * fraction))
    extended = ordered + [
        value + CONNECTION_INTERVAL_US for value in ordered
    ]
    return min(
        extended[index + count - 1] - extended[index]
        for index in range(len(ordered))
    )


def episode_analysis(diagnostics: dict[str, object], duration_s: float) -> dict:
    episodes = diagnostics["episodes"]
    deadlines = unwrap_low_words([
        int(item["first_deadline_low_us"]) for item in episodes
    ])
    relative_phases = (
        [
            (deadline - deadlines[0]) % CONNECTION_INTERVAL_US
            for deadline in deadlines
        ]
        if deadlines
        else []
    )
    quarter = max(1, len(relative_phases) // 4)
    first_phase = relative_phases[:quarter]
    last_phase = relative_phases[-quarter:]
    publish_durations = [
        int(item["publish_duration_us"]) for item in episodes
    ]
    first_lateness = [
        int(item["first_lateness_us"]) for item in episodes
    ]
    overlaps = sum(int(item["publish_overlap"]) for item in episodes)
    missed = sum(int(item["consecutive_misses"]) for item in episodes)
    publish_fields = diagnostics["publish_summary_fields"]
    return {
        "episode_count": len(episodes),
        "episode_rate_per_min": (
            len(episodes) * 60.0 / duration_s if duration_s else None
        ),
        "missed_deadlines": missed,
        "publish_overlap_count": overlaps,
        "publish_overlap_fraction": (
            overlaps / len(episodes) if episodes else None
        ),
        "first_lateness_us": {
            "minimum": min(first_lateness) if first_lateness else None,
            "p50": percentile(first_lateness, 0.50),
            "p95": percentile(first_lateness, 0.95),
            "maximum": max(first_lateness) if first_lateness else None,
        },
        "episode_publish_duration_us": {
            "minimum": min(publish_durations) if publish_durations else None,
            "p50": percentile(publish_durations, 0.50),
            "p95": percentile(publish_durations, 0.95),
            "maximum": max(publish_durations) if publish_durations else None,
        },
        "publish_call": {
            "count": int(publish_fields["n"], 0),
            "maximum_us": int(publish_fields["max"], 0),
            "success": int(publish_fields["ok"], 0),
            "enomem": int(publish_fields["enomem"], 0),
            "other_error": int(publish_fields["other"], 0),
            "duration_saturated": int(publish_fields["sat"], 0),
            "episode_overlap_tally": int(publish_fields["ov"], 0),
            "instrumentation_cycles": int(publish_fields["cyc"], 0),
            "instrumentation_ns": int(publish_fields["ns"], 0),
            "histogram": diagnostics["publish_histogram"],
        },
        "phase_relative_to_first_episode_us": {
            "definition": (
                "unwrapped first-deadline timestamp relative to the first "
                "episode, modulo the fixed 50 ms connection interval"
            ),
            "minimum_circular_span_90pct_us": minimum_circular_span(
                relative_phases, 0.90
            ),
            "first_quarter_median_us": percentile(first_phase, 0.50),
            "last_quarter_median_us": percentile(last_phase, 0.50),
            "controller_anchor_timestamp_available": False,
            "limitation": (
                "DK firmware reports CI and peer index but no controller "
                "connection-event anchor timestamp; concentration and "
                "migration are measurable, absolute anchor alignment is not"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=600.0)
    parser.add_argument("--fusion-port")
    parser.add_argument("--operator-token", required=True)
    args = parser.parse_args()
    if args.operator_token != "POWERED ON":
        raise SessionError("literal operator token POWERED ON is required")

    args.output_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "created_utc": utc_now(),
        "operator_gate": args.operator_token,
        "duration_s": args.duration_s,
        "expected_nodes": BSFS,
        "expected_b306_marker": EXPECTED_B306_MARKER,
        "expected_tag_marker": EXPECTED_TAG_MARKER,
        "fixed_verdict_rule": (
            "a tens-of-ms loss episode overlapping a notify of the same "
            "duration identifies self-blocking in publish; fast publish "
            "during a lateness spike falsifies that mechanism"
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
            channel.send("OUTPUT BINARY")
            assembler = RecordingAssembler()
            collect(channel, assembler, 2.0)
            preflight = request_list(channel, assembler, {}, BSFS)
            strict_link_gate(preflight)
            summary["strict_preflight"] = preflight

            pings: dict[str, object] = {}
            versions: dict[str, object] = {}
            for node in BSFS:
                ping = b306_command(channel, node, "PING", "PONG ")
                if (
                    f"fw={EXPECTED_B306_MARKER} "
                    not in f"{ping['text']} "
                ):
                    raise SessionError(
                        f"{node} B306 marker mismatch: {ping['text']}"
                    )
                pings[node] = ping
                version = relay_command(
                    channel, node, "VERSION", "VERSION ", attempts=3
                )
                if (
                    f"fw={EXPECTED_TAG_MARKER} "
                    not in f"{version['reply']['text']} "
                ):
                    raise SessionError(
                        f"{node} tag marker mismatch: "
                        f"{version['reply']['text']}"
                    )
                versions[node] = version
            summary["b306_pings"] = pings
            summary["tag_versions"] = versions
            write_json(args.output_dir / "summary.json", summary)

            formal = run_one(
                args.output_dir,
                channel,
                5,
                "C",
                args.duration_s,
                int(time.time()) & 0xFF,
                None,
                "N5_C_batch2_notify_blocking",
                imu_batch=2,
            )
            summary["formal"] = formal

            # MODE IDLE is the only valid tag stop on relay3.  Stop UWB
            # before the control-heavy diagnostic download; never CFG_STOP.
            summary["quiet_after_window"] = cleanup(channel)
            diagnostics: dict[str, object] = {}
            analyses: dict[str, object] = {}
            for node in BSFS:
                node_dir = args.output_dir / node
                node_dir.mkdir()
                diagnostic = query_diagnostics(
                    FusionController(channel, node, 8.0, 3),
                    node_dir,
                    2,
                )
                diagnostics[node] = diagnostic
                analyses[node] = episode_analysis(
                    diagnostic, args.duration_s
                )
                write_json(node_dir / "diagnostics.json", diagnostic)
            summary["diagnostics"] = diagnostics
            summary["episode_analysis"] = analyses
            all_episodes = sum(
                item["episode_count"] for item in analyses.values()
            )
            all_overlaps = sum(
                item["publish_overlap_count"] for item in analyses.values()
            )
            max_publish = max(
                item["publish_call"]["maximum_us"]
                for item in analyses.values()
            )
            summary["aggregate_mechanism"] = {
                "episodes": all_episodes,
                "publish_overlaps": all_overlaps,
                "maximum_publish_us": max_publish,
                "identified": (
                    all_episodes > 0
                    and all_overlaps > 0
                    and max_publish >= 20_000
                ),
            }
            summary["status"] = "COMPLETE"
            summary["completed_utc"] = utc_now()
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        summary["completed_utc"] = utc_now()
        if channel is not None:
            try:
                summary["exception_cleanup"] = cleanup(channel)
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
    except (OSError, SessionError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
