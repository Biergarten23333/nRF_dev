#!/usr/bin/env python3
"""Cross-tag SUPERFRAME_BASE and TIMER2 alignment analysis."""

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


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (
        ordered[upper] - ordered[lower]
    ) * (position - lower)


def distribution(values: list[float]) -> dict[str, float | int | None]:
    absolute = [abs(value) for value in values]
    return {
        "count": len(values),
        "mean_us": statistics.mean(values) if values else None,
        "sigma_us": statistics.pstdev(values) if len(values) > 1 else 0.0,
        "absolute_p50_us": percentile(absolute, 0.50),
        "absolute_p95_us": percentile(absolute, 0.95),
        "absolute_p99_us": percentile(absolute, 0.99),
        "absolute_max_us": max(absolute) if absolute else None,
        "signed_min_us": min(values) if values else None,
        "signed_max_us": max(values) if values else None,
    }


def linear_fit(points: dict[int, int]) -> tuple[dict[str, object], dict[int, float]]:
    if len(points) < 3:
        raise ValueError("fewer than three unique sweep/timestamp pairs")
    xs = list(points)
    x0 = statistics.mean(xs)
    ys = [points[x] for x in xs]
    y0 = statistics.mean(ys)
    denominator = sum((x - x0) ** 2 for x in xs)
    slope = sum((x - x0) * (points[x] - y0) for x in xs) / denominator
    intercept = y0 - slope * x0
    residuals = {
        x: points[x] - (intercept + slope * x)
        for x in xs
    }
    return (
        {
            "samples": len(points),
            "first_sweep": min(xs),
            "last_sweep": max(xs),
            "slope_us_per_sweep": slope,
            "slope_error_from_100ms_ppm": (slope / 100_000.0 - 1.0) * 1e6,
            "intercept_us": intercept,
            "residual": distribution(list(residuals.values())),
        },
        residuals,
    )


def parse_rows(
    raw_log: Path,
    nodes: tuple[str, ...],
    start_monotonic: float | None,
    end_monotonic: float | None,
) -> tuple[dict[str, dict[int, int]], dict[str, dict[int, int]], dict[str, int]]:
    timer: dict[str, dict[int, int]] = {node: {} for node in nodes}
    master: dict[str, dict[int, int]] = {node: {} for node in nodes}
    duplicates = {node: 0 for node in nodes}
    for raw in raw_log.read_text(errors="replace").splitlines():
        parts = raw.split(" ", 3)
        if len(parts) < 4 or "FUSION_UWB " not in raw:
            continue
        try:
            monotonic = float(parts[1])
        except ValueError:
            continue
        if start_monotonic is not None and monotonic < start_monotonic:
            continue
        if end_monotonic is not None and monotonic > end_monotonic:
            continue
        record = fields(raw.split("FUSION_UWB ", 1)[1])
        node = record.get("name")
        if node not in timer:
            continue
        if record.get("strobe_us") in (None, "-") or "sweep" not in record:
            continue
        sweep = int(record["sweep"], 0)
        if sweep in timer[node]:
            duplicates[node] += 1
            continue
        timer[node][sweep] = int(record["strobe_us"], 0)
        master[node][sweep] = int(record["master_ms"], 0) * 1000
    return timer, master, duplicates


def cfg_from_setup(setup_path: Path, nodes: tuple[str, ...]) -> dict[str, object]:
    setup = json.loads(setup_path.read_text())
    slots = setup["slots"]
    expected_base = int(slots["superframe_base"])
    rows: dict[str, object] = {}
    for node in nodes:
        node_setup = slots["nodes"][node]
        readback = node_setup.get("cfg_stop_status")
        if readback is None:
            readback = node_setup.get("cfg_status")
        if readback is None:
            raise ValueError(f"{node} has no SUPERFRAME_BASE readback")
        reply = readback["reply"]["text"]
        parsed = {
            key.lower(): value for key, value in fields(reply).items()
        }
        rows[node] = {
            "slot": int(node_setup["slot"]),
            "reported_superframe_base": int(parsed["superframe_base"]),
            "sf_valid": int(parsed["sf_valid"]),
            "run_at_readback": int(parsed["run"]),
            "raw_reply": reply,
        }
    values = {row["reported_superframe_base"] for row in rows.values()}
    return {
        "configured_superframe_base": expected_base,
        "per_node": rows,
        "all_equal": len(values) == 1 and values == {expected_base},
    }


def analyze(
    raw_log: Path,
    setup_path: Path,
    nodes: tuple[str, ...],
    start_monotonic: float | None,
    end_monotonic: float | None,
) -> dict[str, object]:
    timer, master, duplicates = parse_rows(
        raw_log, nodes, start_monotonic, end_monotonic
    )
    cfg = cfg_from_setup(setup_path, nodes)
    fits: dict[str, object] = {}
    residuals: dict[str, dict[int, float]] = {}
    for node in nodes:
        fits[node], residuals[node] = linear_fit(timer[node])

    common = set(timer[nodes[0]])
    union: set[int] = set()
    for node in nodes:
        common &= set(timer[node])
        union |= set(timer[node])
    common_sorted = sorted(common)
    reference = nodes[0]
    relative: dict[str, object] = {}
    master_slot_check: dict[str, object] = {}
    ref_slot = int(cfg["per_node"][reference]["slot"])
    for node in nodes[1:]:
        diffs = [
            residuals[node][sweep] - residuals[reference][sweep]
            for sweep in common_sorted
        ]
        relative[f"{node}-{reference}"] = distribution(diffs)

        slot = int(cfg["per_node"][node]["slot"])
        master_diffs = [
            (master[node][sweep] - master[reference][sweep])
            - (slot - ref_slot) * 10_000
            for sweep in common_sorted
        ]
        master_slot_check[f"{node}-{reference}"] = {
            "known_slot_offset_us": (slot - ref_slot) * 10_000,
            "post_offset_arrival_residual": distribution(master_diffs),
            "limitation": (
                "single-master BLE/USB arrival time is only a coarse fallback; "
                "CI queueing is not a hardware timestamp"
            ),
        }

    index_gate = (
        bool(common)
        and len(common) >= 0.95 * min(len(timer[node]) for node in nodes)
        and len(common) >= 0.95 * len(union)
    )
    return {
        "method": (
            "per-node least-squares TIMER2(strobe_us) versus common UWB sweep; "
            "relative error is residual_i-residual_reference at equal sweep"
        ),
        "selection": {
            "start_monotonic": start_monotonic,
            "end_monotonic": end_monotonic,
        },
        "superframe_base": cfg,
        "parse": {
            "unique_sweeps_per_node": {
                node: len(timer[node]) for node in nodes
            },
            "duplicate_sweeps_ignored": duplicates,
            "common_sweeps": len(common),
            "union_sweeps": len(union),
            "first_common_sweep": common_sorted[0] if common_sorted else None,
            "last_common_sweep": common_sorted[-1] if common_sorted else None,
            "common_index_gate": index_gate,
        },
        "per_node_timer2_fit": fits,
        "equal_index_relative_alignment": relative,
        "known_slot_offset_fallback_check": master_slot_check,
        "gates": {
            "same_superframe_base": cfg["all_equal"],
            "common_sweep_timeline": index_gate,
        },
        "pass": cfg["all_equal"] and index_gate,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_log", type=Path)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--nodes", required=True)
    parser.add_argument("--start-monotonic", type=float)
    parser.add_argument("--end-monotonic", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(
        args.raw_log,
        args.setup,
        tuple(item.strip() for item in args.nodes.split(",") if item.strip()),
        args.start_monotonic,
        args.end_monotonic,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
