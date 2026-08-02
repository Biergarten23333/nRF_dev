#!/usr/bin/env python3
"""Analyze the Phase C-R two-speed JY61P latency diagnostic."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


LINE_RE = re.compile(
    r"IMU LAT (?P<verdict>PASS|FAIL) "
    r"n=(?P<lengths>[0-9,]+) "
    r"u400=(?P<u400>[0-9,]+) "
    r"u100=(?P<u100>[0-9,]+) "
    r"prod=(?P<prod400>\d+)/(?P<prod100>\d+) "
    r"restore=(?P<restore>\d+) "
    r"cfg=(?P<cfg400>-?\d+)/(?P<cfg100>-?\d+)/(?P<cfgrestore>-?\d+) "
    r"xfer=(?P<xfer>-?\d+)"
)


def csv_ints(value: str) -> list[int]:
    return [int(item) for item in value.split(",")]


def linear_fit(xs: list[int], ys: list[int]) -> dict[str, object]:
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator
    intercept = y_mean - slope * x_mean
    predictions = [intercept + slope * x for x in xs]
    residuals = [y - prediction for y, prediction in zip(ys, predictions)]
    ss_res = sum(residual * residual for residual in residuals)
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    r_squared = 1.0 if ss_tot == 0 else 1.0 - ss_res / ss_tot
    return {
        "slope_us_per_byte": slope,
        "intercept_us": intercept,
        "r_squared": r_squared,
        "predicted_us": predictions,
        "residual_us": residuals,
        "naive_software_fixed_a_minus_3b_us": intercept - 3.0 * slope,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    text = args.log.read_text(errors="replace")
    matches = list(LINE_RE.finditer(text))
    if not matches:
        raise SystemExit("no IMU LAT result found")
    match = matches[-1]
    lengths = csv_ints(match["lengths"])
    values_400 = csv_ints(match["u400"])
    values_100 = csv_ints(match["u100"])
    if not (len(lengths) == len(values_400) == len(values_100)):
        raise SystemExit("length/value count mismatch")

    fit_400 = linear_fit(lengths, values_400)
    fit_100 = linear_fit(lengths, values_100)
    bit_time_400_us = 2.5
    bit_time_100_us = 10.0
    control_bits = (
        fit_100["intercept_us"] - fit_400["intercept_us"]
    ) / (bit_time_100_us - bit_time_400_us)
    software_fixed_400 = (
        fit_400["intercept_us"] - control_bits * bit_time_400_us
    )
    software_fixed_100 = (
        fit_100["intercept_us"] - control_bits * bit_time_100_us
    )
    production_length = 26
    data_wire_400 = 9.0 * production_length * bit_time_400_us
    control_wire_400 = control_bits * bit_time_400_us

    result = {
        "source_log": str(args.log),
        "raw_result": match.group(0),
        "diagnostic_verdict": match["verdict"],
        "samples_per_length": 32,
        "length_bytes": lengths,
        "mean_latency_us": {
            "400_khz": values_400,
            "100_khz": values_100,
        },
        "fit": {
            "400_khz": fit_400,
            "100_khz": fit_100,
            "slope_ratio_100_over_400": (
                fit_100["slope_us_per_byte"] / fit_400["slope_us_per_byte"]
            ),
        },
        "exact_production_shape_us": {
            "400_khz": int(match["prod400"]),
            "100_khz": int(match["prod100"]),
            "restored_400_khz": int(match["restore"]),
        },
        "errors": {
            "configure_400": int(match["cfg400"]),
            "configure_100": int(match["cfg100"]),
            "restore_400": int(match["cfgrestore"]),
            "transfer": int(match["xfer"]),
        },
        "naive_three_byte_model": {
            "software_fixed_400_us": fit_400[
                "naive_software_fixed_a_minus_3b_us"
            ],
            "software_fixed_100_us": fit_100[
                "naive_software_fixed_a_minus_3b_us"
            ],
            "difference_us": abs(
                fit_100["naive_software_fixed_a_minus_3b_us"]
                - fit_400["naive_software_fixed_a_minus_3b_us"]
            ),
            "prediction_limit_us": 50.0,
            "verdict": "FAIL",
            "reason": (
                "three returned-byte equivalents omit frequency-scaled "
                "START/reSTART/STOP/control phases"
            ),
        },
        "joint_two_speed_model": {
            "equation": (
                "latency_us = software_fixed_us + "
                "(9*N_bytes + control_bit_equivalents) * bit_time_us"
            ),
            "control_bit_equivalents": control_bits,
            "software_fixed_400_us": software_fixed_400,
            "software_fixed_100_us": software_fixed_100,
            "software_fixed_difference_us": abs(
                software_fixed_100 - software_fixed_400
            ),
            "production_400_khz_breakdown_us": {
                "returned_data": data_wire_400,
                "addressing_and_control": control_wire_400,
                "software_fixed": software_fixed_400,
                "total": data_wire_400 + control_wire_400 + software_fixed_400,
            },
        },
        "channel_skew": {
            "accel_to_same_axis_gyro_byte_offset": 6,
            "400_khz_us": 6.0 * fit_400["slope_us_per_byte"],
            "angle_deg_at_360_deg_s": (
                360.0 * 6.0 * fit_400["slope_us_per_byte"] / 1_000_000.0
            ),
            "angle_deg_at_3_deg_s": (
                3.0 * 6.0 * fit_400["slope_us_per_byte"] / 1_000_000.0
            ),
        },
    }

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
