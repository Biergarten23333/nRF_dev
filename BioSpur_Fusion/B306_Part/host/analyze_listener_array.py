#!/usr/bin/env python3
"""Offline Batch-A coverage and cross-tag origin analysis."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np

from listener_array_collector import (
    DW_LO32_WRAP_SECONDS,
    DW_TICKS_PER_MS,
    LISTENERS,
)


TAG_BY_ID = {
    1: {"bsf": "BSF3C79", "bs": "BS065F"},
    2: {"bsf": "BSFC2CC", "bs": "BSE88E"},
    3: {"bsf": "BSF44AD", "bs": "BS6F3A"},
    4: {"bsf": "BSF6C53", "bs": "BSF8E0"},
    5: {"bsf": "BSF8BC4", "bs": "BSEFD2"},
}
RUNS = {
    "A1": {"dir": "A1_dispersed_600s_retry1", "slots": [0, 2, 4, 6, 8]},
    "A2": {"dir": "A2_dispersed_300s", "slots": [0, 2, 4, 6, 8]},
    "A3": {"dir": "A3_adjacent_300s", "slots": [0, 1, 2, 3, 4]},
}
PERIOD_MS = 10.0
CYCLE_MS = 100.0
SWEEP_BUDGET_MS = 8.5
QUALITY_FIELDS = (
    "carrier_integrator",
    "fp_index",
    "fp1",
    "fp2",
    "fp3",
    "cir_pwr",
    "rxpacc",
    "std_noise",
    "rxtofs",
    "agc",
)


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def percentile(values: list[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


def circular_delta(value: float, reference: float, period: float = CYCLE_MS) -> float:
    return (value - reference + period / 2.0) % period - period / 2.0


def minimal_circular_spread(values: list[float], period: float = CYCLE_MS) -> float | None:
    if len(values) < 2:
        return None
    ordered = sorted(value % period for value in values)
    gaps = [
        ordered[i + 1] - ordered[i] for i in range(len(ordered) - 1)
    ]
    gaps.append(ordered[0] + period - ordered[-1])
    return period - max(gaps)


def formal_bounds(run_dir: Path) -> tuple[float, float]:
    path = run_dir / "fusion_capture" / "capture" / "uwb_timeline.csv"
    values = [
        float(row["capture_monotonic"]) for row in csv.DictReader(path.open())
    ]
    if not values:
        raise RuntimeError(f"empty Fusion UWB timeline: {path}")
    return min(values), max(values)


def continuity_correct(
    rows: list[dict[str, Any]], slot: int
) -> tuple[list[tuple[float, float]], int]:
    """Remove exact low-32 wrap-choice jumps using phase continuity.

    Uptime provided the collector's initial guess.  Here each new sample is
    checked against candidates separated by exactly one 67.216410 ms low-32
    wrap.  The smooth tag phase trajectory chooses the candidate; corrections
    are counted rather than hidden.
    """

    corrected: list[tuple[float, float]] = []
    previous: float | None = None
    corrections = 0
    wrap_ms = DW_LO32_WRAP_SECONDS * 1000.0
    for row in rows:
        observed = (
            row["rx_unwrapped_ticks"] / DW_TICKS_PER_MS
            - slot * PERIOD_MS
        ) % CYCLE_MS
        if previous is None:
            value = observed
        else:
            candidates: list[tuple[float, float, int]] = []
            for wraps in range(-2, 3):
                base = observed + wraps * wrap_ms
                cycles = round((previous - base) / CYCLE_MS)
                value = base + cycles * CYCLE_MS
                candidates.append((abs(value - previous), value, wraps))
            _, value, wraps = min(candidates)
            corrections += int(wraps != 0)
        corrected.append((row["arrival_monotonic_ns"] / 1e9, value))
        previous = value
    return corrected, corrections


def robust_phase_fit(
    rows: list[dict[str, Any]], slot: int, run_mid: float
) -> dict[str, Any] | None:
    if len(rows) < 3:
        return None
    corrected, corrections = continuity_correct(rows, slot)
    x = np.asarray([stamp - run_mid for stamp, _ in corrected], dtype=float)
    y = np.asarray([value for _, value in corrected], dtype=float)
    keep = np.ones(len(x), dtype=bool)
    residual = np.zeros(len(x), dtype=float)
    for _ in range(4):
        slope, intercept = np.polyfit(x[keep], y[keep], 1)
        residual = y - (slope * x + intercept)
        center = float(np.median(residual[keep]))
        mad = float(np.median(np.abs(residual[keep] - center)))
        threshold = max(0.050, 8.0 * 1.4826 * mad)
        new_keep = np.abs(residual - center) <= threshold
        if new_keep.sum() < 3 or np.array_equal(new_keep, keep):
            break
        keep = new_keep
    slope, intercept = np.polyfit(x[keep], y[keep], 1)
    residual = y - (slope * x + intercept)
    return {
        "records": len(rows),
        "fit_records": int(keep.sum()),
        "phase_at_mid_ms": float(intercept % CYCLE_MS),
        "slope_ms_per_s": float(slope),
        "clock_offset_ppm_vs_listener": float(slope * 1000.0),
        "residual_p50_us": percentile(list(np.abs(residual[keep]) * 1000.0), 50),
        "residual_p95_us": percentile(list(np.abs(residual[keep]) * 1000.0), 95),
        "lo32_continuity_corrections": corrections,
    }


def listener_records(
    path: Path, start: float, end: float
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
    polls = {tag: [] for tag in TAG_BY_ID}
    all_kinds: dict[str, int] = {}
    first_lstat = None
    last_lstat = None
    parse_errors = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            stamp = row["arrival_monotonic_ns"] / 1e9
            if not start <= stamp <= end:
                continue
            kind = row["kind"]
            all_kinds[kind] = all_kinds.get(kind, 0) + 1
            if not row["parsed_ok"]:
                parse_errors += 1
                continue
            if kind == "LSTAT":
                if first_lstat is None:
                    first_lstat = row["fields"]
                last_lstat = row["fields"]
            if kind != "LPD":
                continue
            tag = row["fields"].get("tag_id")
            if tag in polls:
                polls[tag].append(row)
    transport = {
        "kinds": all_kinds,
        "parse_errors": parse_errors,
        "lstat_delta": None,
    }
    if first_lstat is not None and last_lstat is not None:
        transport["lstat_delta"] = {
            key: last_lstat.get(key, 0) - first_lstat.get(key, 0)
            for key in (
                "good_frames",
                "accepted_polls",
                "rx_errors",
                "ring_drops",
                "self_recover",
                "rx_enable_failures",
            )
        }
    return polls, transport


def analyze_listener(
    path: Path,
    slots: list[int],
    start: float,
    end: float,
) -> dict[str, Any]:
    polls, transport = listener_records(path, start, end)
    duration = end - start
    expected = duration * 10.0
    run_mid = (start + end) / 2.0
    tags: dict[str, Any] = {}
    fits: dict[int, dict[str, Any]] = {}
    for tag, rows in polls.items():
        bins = []
        bin_count = max(1, math.ceil(duration / 30.0))
        for index in range(bin_count):
            count = sum(
                index
                == min(
                    bin_count - 1,
                    int(
                        (row["arrival_monotonic_ns"] / 1e9 - start)
                        // 30.0
                    ),
                )
                for row in rows
            )
            bins.append(
                {
                    "bin": index,
                    "count": count,
                    "expected": min(300.0, max(0.0, expected - index * 300.0)),
                    "rate": count
                    / min(300.0, max(0.0, expected - index * 300.0)),
                }
            )
        quality = {
            field: {
                "median": (
                    float(statistics.median(values)) if values else None
                ),
                "p05": percentile(values, 5),
                "p95": percentile(values, 95),
            }
            for field in QUALITY_FIELDS
            if (
                values := [
                    float(row["fields"][field])
                    for row in rows
                    if field in row["fields"]
                ]
            )
        }
        fit = robust_phase_fit(rows, slots[tag - 1], run_mid)
        if fit is not None:
            fits[tag] = fit
        tags[str(tag)] = {
            **TAG_BY_ID[tag],
            "slot": slots[tag - 1],
            "polls": len(rows),
            "expected_polls": expected,
            "poll_reception_rate": len(rows) / expected,
            "bins_30s": bins,
            "quality": quality,
            "phase_fit": fit,
        }

    pairwise: dict[str, Any] = {}
    for left, right in combinations(TAG_BY_ID, 2):
        key = f"{left}-{right}"
        if left not in fits or right not in fits:
            pairwise[key] = None
            continue
        pairwise[key] = {
            "origin_difference_ms": circular_delta(
                fits[right]["phase_at_mid_ms"],
                fits[left]["phase_at_mid_ms"],
            ),
            "differential_drift_ppm": (
                fits[right]["slope_ms_per_s"]
                - fits[left]["slope_ms_per_s"]
            )
            * 1000.0,
        }
    phases = [
        fits[tag]["phase_at_mid_ms"] for tag in TAG_BY_ID if tag in fits
    ]
    return {
        "transport": transport,
        "tags": tags,
        "pairwise": pairwise,
        "origin_spread_ms": (
            minimal_circular_spread(phases) if len(phases) == 5 else None
        ),
        "phase_tags_available": sorted(fits),
    }


def aggregate_run(run: dict[str, Any], slots: list[int]) -> dict[str, Any]:
    listeners = run["listeners"]
    pairwise: dict[str, Any] = {}
    for left, right in combinations(TAG_BY_ID, 2):
        key = f"{left}-{right}"
        rows = [
            value["pairwise"][key]
            for value in listeners.values()
            if value["pairwise"][key] is not None
        ]
        diffs = [row["origin_difference_ms"] for row in rows]
        if not diffs:
            pairwise[key] = None
            continue
        reference = statistics.median(diffs)
        unwrapped = [
            reference + circular_delta(value, reference) for value in diffs
        ]
        median_diff = statistics.median(unwrapped)
        drift = [row["differential_drift_ppm"] for row in rows]
        physical_separation = abs(
            circular_delta(
                median_diff + (slots[right - 1] - slots[left - 1]) * PERIOD_MS,
                0.0,
            )
        )
        pairwise[key] = {
            "listeners": len(rows),
            "origin_difference_median_ms": median_diff,
            "between_listener_range_ns": (
                max(unwrapped) - min(unwrapped)
            )
            * 1e6,
            "between_listener_std_ns": float(np.std(unwrapped)) * 1e6,
            "differential_drift_median_ppm": statistics.median(drift),
            "differential_drift_range_ppm": [min(drift), max(drift)],
            "physical_sweep_start_separation_ms": physical_separation,
            "predicted_overlap": physical_separation < SWEEP_BUDGET_MS,
        }
    spreads = [
        value["origin_spread_ms"]
        for value in listeners.values()
        if value["origin_spread_ms"] is not None
    ]
    coverage = {}
    for tag in TAG_BY_ID:
        rates = [
            value["tags"][str(tag)]["poll_reception_rate"]
            for value in listeners.values()
        ]
        coverage[str(tag)] = {
            **TAG_BY_ID[tag],
            "listener_min": min(rates),
            "listener_median": statistics.median(rates),
            "listener_max": max(rates),
        }
    return {
        "pairwise": pairwise,
        "origin_spread_median_ms": (
            statistics.median(spreads) if spreads else None
        ),
        "origin_spread_range_ms": (
            [min(spreads), max(spreads)] if spreads else None
        ),
        "coverage_by_tag": coverage,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "constants": {
            "cycle_ms": CYCLE_MS,
            "period_ms": PERIOD_MS,
            "sweep_budget_ms": SWEEP_BUDGET_MS,
            "low32_wrap_ms": DW_LO32_WRAP_SECONDS * 1000.0,
            "propagation_bound_ns": 18.4,
        },
        "runs": {},
    }
    for label, spec in RUNS.items():
        run_dir = args.root / spec["dir"]
        start, end = formal_bounds(run_dir)
        listener_results = {}
        for listener in LISTENERS:
            path = (
                run_dir
                / "listener_capture"
                / "listeners"
                / f"{listener.snr}.jsonl"
            )
            listener_results[listener.snr] = analyze_listener(
                path, spec["slots"], start, end
            )
        run = {
            "source_dir": str(run_dir.resolve()),
            "slots": spec["slots"],
            "formal_start_monotonic": start,
            "formal_end_monotonic": end,
            "formal_duration_s": end - start,
            "listeners": listener_results,
        }
        run["aggregate"] = aggregate_run(run, spec["slots"])
        fusion_summary = json.loads(
            (run_dir / "fusion_capture" / "summary.json").read_text()
        )
        run["fusion_validity"] = {
            node: {
                "records": row["records"],
                "valid_fraction": row["valid_fraction"],
            }
            for node, row in fusion_summary["uwb_analysis"]["per_node"].items()
        }
        run["fusion_pairwise_contingency"] = fusion_summary["uwb_analysis"][
            "pairwise_contingency"
        ]
        result["runs"][label] = run
        write_json(args.out / f"{label}_analysis.json", run)
    write_json(args.out / "listener_array_analysis.json", result)

    with (args.out / "coverage.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = [
            "run",
            "listener_snr",
            "listener_key",
            "designated_beacon",
            "tag_id",
            "bsf",
            "bs",
            "slot",
            "polls",
            "expected_polls",
            "poll_reception_rate",
        ] + [f"{field}_median" for field in QUALITY_FIELDS]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        listener_by_snr = {item.snr: item for item in LISTENERS}
        for label, run in result["runs"].items():
            for snr, listener_row in run["listeners"].items():
                listener = listener_by_snr[snr]
                for tag, tag_row in listener_row["tags"].items():
                    row = {
                        "run": label,
                        "listener_snr": snr,
                        "listener_key": listener.key,
                        "designated_beacon": int(listener.designated_beacon),
                        "tag_id": tag,
                        "bsf": tag_row["bsf"],
                        "bs": tag_row["bs"],
                        "slot": tag_row["slot"],
                        "polls": tag_row["polls"],
                        "expected_polls": tag_row["expected_polls"],
                        "poll_reception_rate": tag_row[
                            "poll_reception_rate"
                        ],
                    }
                    for field in QUALITY_FIELDS:
                        row[f"{field}_median"] = tag_row["quality"].get(
                            field, {}
                        ).get("median")
                    writer.writerow(row)

    with (args.out / "coverage_bins_30s.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = [
            "run",
            "listener_snr",
            "tag_id",
            "bsf",
            "bin",
            "count",
            "expected",
            "rate",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for label, run in result["runs"].items():
            for snr, listener_row in run["listeners"].items():
                for tag, tag_row in listener_row["tags"].items():
                    for bin_row in tag_row["bins_30s"]:
                        writer.writerow(
                            {
                                "run": label,
                                "listener_snr": snr,
                                "tag_id": tag,
                                "bsf": tag_row["bsf"],
                                **bin_row,
                            }
                        )

    with (args.out / "phase_pairwise.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = [
            "run",
            "pair",
            "listeners",
            "origin_difference_median_ms",
            "between_listener_range_ns",
            "between_listener_std_ns",
            "differential_drift_median_ppm",
            "physical_sweep_start_separation_ms",
            "predicted_overlap",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for label, run in result["runs"].items():
            for pair, row in run["aggregate"]["pairwise"].items():
                writer.writerow(
                    {"run": label, "pair": pair}
                    | ({field: None for field in fields[2:]} if row is None else {
                        field: row.get(field) for field in fields[2:]
                    })
                )

    print(
        json.dumps(
            {
                label: {
                    "spread_ms": run["aggregate"]["origin_spread_median_ms"],
                    "spread_range_ms": run["aggregate"][
                        "origin_spread_range_ms"
                    ],
                    "fusion_validity": run["fusion_validity"],
                }
                for label, run in result["runs"].items()
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
