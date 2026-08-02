#!/usr/bin/env python3
"""Correlate IMU sequence gaps with B306 health recoveries in a raw log."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


FIELD_RE = re.compile(r"(\w+)=([^ ]+)")


def fields(line: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in FIELD_RE.finditer(line)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_log", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    previous_imu: dict[str, dict[str, int | float]] = {}
    previous_hreset: dict[str, int] = {}
    gaps: list[dict[str, int | float | str | None]] = []
    recoveries: list[dict[str, int | float | str | None]] = []

    with args.raw_log.open(encoding="utf-8", errors="replace") as source:
        for raw in source:
            split = raw.rstrip().split(" FUSION_RX ", 1)
            if len(split) != 2:
                continue
            prefix, payload = split
            host_timestamp = float(prefix.split()[0])
            parsed = fields(payload)
            node = parsed.get("name")
            if node is None:
                continue

            if payload.startswith("FUSION_IMU "):
                sequence = int(parsed["seq"], 0)
                count = int(parsed["n"], 0)
                base_us = int(parsed["base_us"], 0)
                previous = previous_imu.get(node)
                if previous is not None:
                    expected = (
                        int(previous["sequence"]) + int(previous["count"])
                    ) & 0xFFFF
                    if sequence != expected:
                        base_delta_us = base_us - int(previous["base_us"])
                        nominal_delta_us = int(previous["count"]) * 5_000
                        gaps.append(
                            {
                                "node": node,
                                "host_timestamp": host_timestamp,
                                "previous_sequence": previous["sequence"],
                                "previous_count": previous["count"],
                                "expected_sequence": expected,
                                "actual_sequence": sequence,
                                "extra_sequence_samples": (
                                    sequence - expected
                                )
                                & 0xFFFF,
                                "previous_base_us": previous["base_us"],
                                "base_us": base_us,
                                "base_delta_us": base_delta_us,
                                "recovery_pause_above_nominal_us": (
                                    base_delta_us - nominal_delta_us
                                ),
                            }
                        )
                previous_imu[node] = {
                    "sequence": sequence,
                    "count": count,
                    "base_us": base_us,
                    "host_timestamp": host_timestamp,
                }
                continue

            if not payload.startswith("FUSION_TELEMETRY "):
                continue
            if "imu_hreset" not in parsed:
                continue
            current = int(parsed["imu_hreset"], 0)
            previous = previous_hreset.get(node)
            # A downward change is a counter baseline/reset, not a recovery.
            if previous is not None and current > previous:
                recoveries.append(
                    {
                        "node": node,
                        "host_timestamp": host_timestamp,
                        "previous_count": previous,
                        "current_count": current,
                        "node_ms": int(parsed["node_ms"], 0),
                        "fault_us": int(parsed["imu_fault_us"], 0),
                        "recovered_us": int(parsed["imu_recovered_us"], 0),
                        "imu_i2c_err": int(parsed["imu_i2c_err"], 0),
                        "imu_hrate": int(parsed["imu_hrate"], 0),
                    }
                )
            previous_hreset[node] = current

    unmatched_recoveries = set(range(len(recoveries)))
    for gap in gaps:
        candidates = [
            (index, recovery)
            for index, recovery in enumerate(recoveries)
            if index in unmatched_recoveries
            and recovery["node"] == gap["node"]
            and 0.0
            <= float(recovery["host_timestamp"])
            - float(gap["host_timestamp"])
            <= 2.0
        ]
        if not candidates:
            gap["matched_recovery_index"] = None
            gap["recovery_report_delay_ms"] = None
            continue
        index, recovery = min(
            candidates,
            key=lambda item: float(item[1]["host_timestamp"])
            - float(gap["host_timestamp"]),
        )
        unmatched_recoveries.remove(index)
        gap["matched_recovery_index"] = index
        gap["recovery_report_delay_ms"] = (
            float(recovery["host_timestamp"])
            - float(gap["host_timestamp"])
        ) * 1_000.0

    per_node: dict[str, dict[str, int]] = {}
    for node in sorted(
        {str(row["node"]) for row in gaps + recoveries}
    ):
        node_gaps = [row for row in gaps if row["node"] == node]
        node_recoveries = [
            row for row in recoveries if row["node"] == node
        ]
        per_node[node] = {
            "sequence_gaps": len(node_gaps),
            "health_recoveries": len(node_recoveries),
            "matched_gaps": sum(
                row["matched_recovery_index"] is not None
                for row in node_gaps
            ),
        }

    result = {
        "source": str(args.raw_log),
        "sequence_gaps": gaps,
        "health_recoveries": recoveries,
        "unmatched_recovery_indices": sorted(unmatched_recoveries),
        "per_node": per_node,
        "all_gaps_match_one_recovery": all(
            row["matched_recovery_index"] is not None for row in gaps
        ),
        "all_recoveries_consumed": not unmatched_recoveries,
    }
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
