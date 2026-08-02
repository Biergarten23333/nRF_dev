#!/usr/bin/env python3
"""Recover the completed N5-C window after post-window cleanup timed out."""

from __future__ import annotations

import json
from pathlib import Path

from capacity_ramp import (
    BSFS,
    RecordingAssembler,
    analyze_run,
)
from fusion_session import parse_fields


ROOT = Path(
    "B306_Part/logs/capacity_ramp_20260727/formal_ramp_v21_r5"
)
RUN = ROOT / "N5_C"


def read_log(path: Path):
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            parts = raw.rstrip("\n").split(" ", 3)
            if len(parts) != 4:
                continue
            try:
                epoch = float(parts[0])
                monotonic = float(parts[1])
            except ValueError:
                continue
            yield epoch, monotonic, parts[2], parts[3]


def snapshot_command(
    records: list[tuple[float, float, str, str]],
    command: str,
    before: float,
) -> list[str]:
    starts = [
        index
        for index, (_, stamp, direction, payload) in enumerate(records)
        if stamp < before
        and direction == "FUSION_TX"
        and payload == command
    ]
    if not starts:
        raise RuntimeError(f"no {command!r} command before formal window")
    lines: list[str] = []
    for _, _, direction, payload in records[starts[-1] + 1 :]:
        if direction == "FUSION_TX":
            break
        if direction == "FUSION_RX":
            lines.append(payload)
    return lines


def list_snapshot(lines: list[str]) -> dict:
    aggregate = [
        parse_fields(line)
        for line in lines
        if line.startswith("FUSION_LIST ")
    ]
    peers = {
        fields["name"]: fields
        for line in lines
        if line.startswith("FUSION_PEER ")
        and (fields := parse_fields(line)).get("name") in BSFS
    }
    if not aggregate or set(peers) != set(BSFS):
        raise RuntimeError("incomplete LIST snapshot")
    return {"aggregate": aggregate[-1], "peers": peers}


def resource_snapshot(lines: list[str]) -> dict:
    summaries = [
        parse_fields(line)
        for line in lines
        if line.startswith("FUSION_RESOURCE_SUMMARY ")
    ]
    if not summaries:
        raise RuntimeError("incomplete RESOURCES snapshot")
    return {
        "summary": summaries[-1],
        "pools": [
            parse_fields(line)
            for line in lines
            if line.startswith("FUSION_RESOURCE_POOL ")
        ],
        "stacks": [
            parse_fields(line)
            for line in lines
            if line.startswith("FUSION_STACK ")
        ],
    }


def main() -> None:
    setup = json.loads((RUN / "setup.json").read_text())
    start_correlations = {
        int(item["correlation"])
        for item in setup["imu_start"].values()
    }
    records = list(read_log(ROOT / "fusion_raw.log"))
    start_acks = [
        stamp
        for _, stamp, direction, payload in records
        if direction == "FUSION_RX"
        and payload.startswith("FUSION_REPLY ")
        and parse_fields(payload).get("source") == "B306"
        and int(parse_fields(payload).get("correlation", "-1")) in start_correlations
        and "text=IMU START OK " in payload
    ]
    start_txs = [
        stamp
        for _, stamp, direction, payload in records
        if direction == "FUSION_TX"
        and payload.endswith(" IMU START")
        and stamp <= max(start_acks)
    ]
    if len(start_acks) != 5 or not start_txs:
        raise RuntimeError(
            f"could not identify five starts: acks={start_acks}"
        )
    formal_start = max(start_acks)
    baseline_cutoff = min(
        stamp for stamp in start_txs if stamp >= min(start_acks) - 1.0
    )
    formal_end_candidates = [
        stamp
        for _, stamp, direction, payload in records
        if direction == "FUSION_TX"
        and payload.endswith(" IMU STATUS")
        and stamp >= formal_start + 299.0
    ]
    if not formal_end_candidates:
        raise RuntimeError("post-window IMU STATUS boundary not found")
    formal_end = min(formal_end_candidates)

    baseline_assembler = RecordingAssembler()
    for _, stamp, direction, payload in records:
        if stamp >= baseline_cutoff:
            break
        if direction == "FUSION_RX":
            baseline_assembler.observe(payload)
    baseline = {
        name: dict(baseline_assembler.latest[name]) for name in BSFS
    }

    assembler = RecordingAssembler()
    rows: list[tuple[float, str]] = []
    for _, stamp, direction, payload in records:
        if direction != "FUSION_RX":
            continue
        if formal_start <= stamp < formal_end:
            assembler.observe(payload)
            rows.append((stamp, payload))

    final_assembler = RecordingAssembler()
    for _, _, direction, payload in read_log(
        ROOT / "emergency_cleanup.log"
    ):
        if direction == "FUSION_RX":
            final_assembler.observe(payload)
    final = {name: dict(final_assembler.latest[name]) for name in BSFS}

    start_list = list_snapshot(
        snapshot_command(records, "LIST", baseline_cutoff)
    )
    start_resources = resource_snapshot(
        snapshot_command(records, "RESOURCES", baseline_cutoff)
    )
    post = json.loads((RUN / "post_cleanup_snapshot.json").read_text())
    prediction = json.loads((RUN / "predictions.json").read_text())
    analysis = analyze_run(
        rows,
        assembler,
        baseline,
        final,
        formal_end - formal_start,
        5,
        "C",
        start_list,
        post["end_list"],
        start_resources,
        post["end_resources"],
        prediction,
    )
    analysis["started_monotonic"] = formal_start
    analysis["ended_monotonic"] = formal_end
    analysis["recovery"] = {
        "reason": (
            "formal 300 s window completed; original process stopped after "
            "the first post-window IMU STOP acknowledgement timed out"
        ),
        "baseline_cutoff_monotonic": baseline_cutoff,
        "formal_start_definition": "last of five IMU START OK replies",
        "formal_end_definition": "first post-window IMU STATUS transmission",
        "source_raw_log": str(ROOT / "fusion_raw.log"),
        "source_cleanup_log": str(ROOT / "emergency_cleanup.log"),
    }
    (RUN / "analysis.json").write_text(
        json.dumps(analysis, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "duration_s": analysis["duration_s"],
                "pass": analysis["pass"],
                "gates": analysis["gates"],
                "delivered_notifications_s": analysis["aggregate"][
                    "delivered_notifications_s"
                ],
                "latency": analysis["aggregate"]["latency"][
                    "lower_envelope_normalized_latency_us"
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
