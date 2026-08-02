#!/usr/bin/env python3
"""Fit B306 TIMER2 strobe time against the UWB sweep index."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path


FIELD_RE = re.compile(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")


def fields(line: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in FIELD_RE.finditer(line)}


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return (
        ordered[lower]
        + (ordered[upper] - ordered[lower]) * (position - lower)
    )


def parse_rows(
    path: Path,
    between_imu_start_stop: bool,
    limit: int | None,
) -> tuple[list[tuple[int, int]], dict[str, int]]:
    active = not between_imu_start_stop
    rows: list[tuple[int, int]] = []
    counters = {
        "derived_orphan_timestamp": 0,
        "ble_disconnects": 0,
        "ble_connections": 0,
        "rejected_nonmonotonic": 0,
    }
    last_sweep: int | None = None
    last_timestamp: int | None = None
    for raw in path.read_text(errors="replace").splitlines():
        if "FUSION_CONNECTED " in raw:
            counters["ble_connections"] += 1
        if "FUSION_DISCONNECTED " in raw:
            counters["ble_disconnects"] += 1
        if between_imu_start_stop and "text=IMU START OK " in raw:
            active = True
            continue
        if (
            between_imu_start_stop
            and active
            and " FUSION_TX " in raw
            and raw.endswith(" IMU STOP")
        ):
            break
        if not active or "FUSION_UWB " not in raw:
            continue
        record = fields(raw.split("FUSION_UWB ", 1)[1])
        if "sweep" not in record:
            continue
        timestamp = record.get("strobe_us")
        if timestamp == "-" and record.get("verdict") == "b306_missed_edge":
            timestamp = record.get("last_orphan_us")
            counters["derived_orphan_timestamp"] += 1
        if timestamp in (None, "-"):
            continue
        sweep = int(record["sweep"], 0)
        timer_us = int(timestamp, 0)
        if (
            last_sweep is not None
            and last_timestamp is not None
            and (sweep <= last_sweep or timer_us <= last_timestamp)
        ):
            counters["rejected_nonmonotonic"] += 1
            continue
        rows.append((sweep, timer_us))
        last_sweep = sweep
        last_timestamp = timer_us
        if limit is not None and len(rows) >= limit:
            break
    if len(rows) < 3:
        raise ValueError("fewer than three monotonic strobe/sweep pairs")
    return rows, counters


def fit(rows: list[tuple[int, int]]) -> dict[str, object]:
    x_mean = statistics.mean(sweep for sweep, _ in rows)
    y_mean = statistics.mean(timer for _, timer in rows)
    slope = sum(
        (sweep - x_mean) * (timer - y_mean) for sweep, timer in rows
    ) / sum((sweep - x_mean) ** 2 for sweep, _ in rows)
    intercept = y_mean - slope * x_mean
    residuals = [
        timer - (intercept + slope * sweep) for sweep, timer in rows
    ]
    absolute = [abs(value) for value in residuals]
    sigma = statistics.pstdev(residuals)
    skew = (
        sum(value**3 for value in residuals)
        / len(residuals)
        / sigma**3
        if sigma
        else 0.0
    )
    lag1 = (
        sum(
            residuals[index] * residuals[index - 1]
            for index in range(1, len(residuals))
        )
        / sum(value * value for value in residuals)
        if sigma
        else 0.0
    )
    modulo_means = {}
    for modulus in (8, 10, 35):
        modulo_means[str(modulus)] = {
            str(remainder): statistics.mean(
                residual
                for (sweep, _), residual in zip(rows, residuals)
                if sweep % modulus == remainder
            )
            for remainder in range(modulus)
            if any(sweep % modulus == remainder for sweep, _ in rows)
        }

    return {
        "samples": len(rows),
        "first_sweep": rows[0][0],
        "last_sweep": rows[-1][0],
        "first_timer_us": rows[0][1],
        "last_timer_us": rows[-1][1],
        "slope_us_per_sweep": slope,
        "slope_error_from_100ms_ppm": (slope / 100000.0 - 1.0) * 1e6,
        "intercept_us": intercept,
        "residual": {
            "mean_us": statistics.mean(residuals),
            "sigma_us": sigma,
            "absolute_p95_us": percentile(absolute, 0.95),
            "absolute_p99_us": percentile(absolute, 0.99),
            "absolute_max_us": max(absolute),
            "signed_min_us": min(residuals),
            "signed_max_us": max(residuals),
            "skew": skew,
            "positive_fraction": sum(value > 0 for value in residuals)
            / len(residuals),
            "negative_fraction": sum(value < 0 for value in residuals)
            / len(residuals),
            "lag1_autocorrelation": lag1,
            "sweep_modulo_mean_us": modulo_means,
        },
        "iid_zero_mean_offset_prediction": {
            "five_minute_3000_sweeps_us": sigma / math.sqrt(3000),
            "thirty_minute_18000_sweeps_us": sigma / math.sqrt(18000),
            "assumption": (
                "valid only for zero-mean independent residuals; lag-1 and "
                "modulo structure must be checked before treating it as an "
                "accuracy bound"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--between-imu-start-stop", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rows, counters = parse_rows(
        args.log, args.between_imu_start_stop, args.limit
    )
    result = {
        "label": args.label,
        "source": str(args.log),
        "selection": {
            "between_imu_start_stop": args.between_imu_start_stop,
            "limit": args.limit,
        },
        "parse_counters": counters,
        "fit": fit(rows),
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
