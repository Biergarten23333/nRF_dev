#!/usr/bin/env python3
"""Sliding TIMER2-versus-sweep fits for TDMA clock-drift analysis."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from analyze_multiunit_alignment import fit, parse_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--window-sweeps", type=int, default=3000)
    parser.add_argument("--stride-sweeps", type=int, default=600)
    args = parser.parse_args()

    rows, counters = parse_rows(args.log, False, None)
    windows = []
    for start in range(
        0, len(rows) - args.window_sweeps + 1, args.stride_sweeps
    ):
        selected = rows[start : start + args.window_sweeps]
        result = fit(selected)
        windows.append(
            {
                "start_row": start,
                "end_row": start + args.window_sweeps - 1,
                "mid_elapsed_s": (
                    selected[len(selected) // 2][1] - rows[0][1]
                )
                / 1e6,
                "first_sweep": selected[0][0],
                "last_sweep": selected[-1][0],
                "slope_us_per_sweep": result["slope_us_per_sweep"],
                "slope_error_ppm": result[
                    "slope_error_from_100ms_ppm"
                ],
                "residual_sigma_us": result["residual"]["sigma_us"],
                "residual_abs_p95_us": result["residual"][
                    "absolute_p95_us"
                ],
                "residual_abs_max_us": result["residual"][
                    "absolute_max_us"
                ],
                "lag1": result["residual"]["lag1_autocorrelation"],
            }
        )
    slopes = [window["slope_error_ppm"] for window in windows]
    elapsed_s = (rows[-1][1] - rows[0][1]) / 1e6
    payload = {
        "source": str(args.log),
        "parse_counters": counters,
        "available": {
            "rows": len(rows),
            "elapsed_s": elapsed_s,
            "elapsed_min": elapsed_s / 60.0,
            "first_sweep": rows[0][0],
            "last_sweep": rows[-1][0],
            "censored_before_one_hour": elapsed_s < 3600.0,
        },
        "window": {
            "sweeps": args.window_sweeps,
            "stride_sweeps": args.stride_sweeps,
            "nominal_minutes": args.window_sweeps / 600.0,
        },
        "slope": {
            "window_count": len(slopes),
            "minimum_ppm": min(slopes),
            "maximum_ppm": max(slopes),
            "peak_to_peak_ppm": max(slopes) - min(slopes),
            "mean_ppm": statistics.mean(slopes),
            "first_ppm": slopes[0],
            "last_ppm": slopes[-1],
            "last_minus_first_ppm": slopes[-1] - slopes[0],
        },
        "windows": windows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload["available"], indent=2))
    print(json.dumps(payload["slope"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
