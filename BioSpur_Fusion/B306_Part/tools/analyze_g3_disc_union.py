#!/usr/bin/env python3
"""Analyze the preregistered S5a + G3-DISC same-observer union."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = (
    ROOT
    / "UWB_Part/logs/night_20260730/morning/final_relay7_1800s/"
    "analyze_final_relay7.py"
)
OBSERVERS = ("760184548", "760184753", "760184767", "760184784", "760184964")
TAG_TO_BSF = {
    1: "BSF3C79",
    2: "BSFC2CC",
    3: "BSF44AD",
    4: "BSF6C53",
    5: "BSF8BC4",
}
OLD_SLOTS = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
NEW_SLOTS = {1: 1, 2: 5, 3: 3, 4: 4, 5: 2}
BRIDGE_TAGS = (1, 3, 4)


def load_reference():
    spec = importlib.util.spec_from_file_location("relay7_analysis_g3", REFERENCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load reference analysis: {REFERENCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_summary_bounds(path: Path, prefix: str) -> tuple[int, int]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    return int(obj[f"{prefix}started_monotonic_ns"]), int(
        obj[f"{prefix}ended_monotonic_ns"]
    )


def support(rows: list[dict], tag_id: int) -> int:
    return sum(
        row.get("kind") == "LPD"
        and row.get("parsed_ok")
        and row.get("fields", {}).get("tag_id") == tag_id
        for row in rows
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--s5a", required=True, type=Path)
    parser.add_argument("--disc-listeners", required=True, type=Path)
    parser.add_argument("--disc-summary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    ref = load_reference()

    old_start, old_end = load_summary_bounds(args.s5a / "summary.json", "formal_")
    new_start, new_end = load_summary_bounds(args.disc_summary, "")
    old_duration = (old_end - old_start) / 1e9
    new_duration = (new_end - new_start) / 1e9

    per_observer: dict[str, object] = {}
    candidates: list[tuple[int, int, str]] = []
    lowest_old: Counter[int] = Counter()
    lowest_new: Counter[int] = Counter()

    for snr in OBSERVERS:
        old_path = args.s5a / "listeners/listeners" / f"{snr}.jsonl"
        new_path = args.disc_listeners / "listeners" / f"{snr}.jsonl"
        old_rows = ref.load_rows(old_path, old_start, old_end)
        new_rows = ref.load_rows(new_path, new_start, new_end)
        windows = {"s5a": {}, "disc": {}}
        samples_by_window: dict[str, dict[int, list[dict]]] = {
            "s5a": {},
            "disc": {},
        }
        for tag_id in TAG_TO_BSF:
            old_measurement, old_samples = ref.lock_measurement(
                old_rows, old_start, tag_id, OLD_SLOTS[tag_id]
            )
            new_measurement, new_samples = ref.lock_measurement(
                new_rows, new_start, tag_id, NEW_SLOTS[tag_id]
            )
            windows["s5a"][tag_id] = {
                **old_measurement,
                "lpd_rate_hz": support(old_rows, tag_id) / old_duration,
            }
            windows["disc"][tag_id] = {
                **new_measurement,
                "lpd_rate_hz": support(new_rows, tag_id) / new_duration,
            }
            samples_by_window["s5a"][tag_id] = old_samples
            samples_by_window["disc"][tag_id] = new_samples

        old_rates = {
            tag: windows["s5a"][tag]["lpd_rate_hz"] for tag in TAG_TO_BSF
        }
        new_rates = {
            tag: windows["disc"][tag]["lpd_rate_hz"] for tag in TAG_TO_BSF
        }
        lowest_old[min(old_rates, key=old_rates.get)] += 1
        lowest_new[min(new_rates, key=new_rates.get)] += 1

        best_support = {
            tag: max(
                len(samples_by_window["s5a"][tag]),
                len(samples_by_window["disc"][tag]),
            )
            for tag in TAG_TO_BSF
        }
        candidates.append((min(best_support.values()), sum(best_support.values()), snr))
        per_observer[snr] = {
            "windows": windows,
            "union_best_support": best_support,
        }

    # Coverage verdict is deliberately based on which identity occupies the
    # lowest-coverage rank, not on phase values.
    old_lowest = lowest_old.most_common(1)[0][0]
    new_lowest = lowest_new.most_common(1)[0][0]
    if old_lowest == 2 and new_lowest == 5:
        coverage_verdict = "FOLLOWS_SLOT_INSTRUMENTAL"
    elif old_lowest == 2 and new_lowest == 2:
        coverage_verdict = "FOLLOWS_TAG_PHYSICAL"
    else:
        coverage_verdict = "MIXED_UNRESOLVED"

    # Selection is support-only and therefore independent of phase spread.
    min_support, total_support, chosen = max(candidates)
    chosen_windows = per_observer[chosen]["windows"]
    bridge_deltas = []
    for tag in BRIDGE_TAGS:
        old_row = chosen_windows["s5a"][tag]
        new_row = chosen_windows["disc"][tag]
        if old_row["status"] != "OK" or new_row["status"] != "OK":
            raise RuntimeError(f"chosen observer lacks bridge tag {tag}")
        bridge_deltas.append(
            float(old_row["median_residual_us"])
            - float(new_row["median_residual_us"])
        )
    correction_us = statistics.median(bridge_deltas)

    medians: dict[str, float] = {}
    sources: dict[str, str] = {}
    for tag, name in TAG_TO_BSF.items():
        old_row = chosen_windows["s5a"][tag]
        new_row = chosen_windows["disc"][tag]
        old_n = int(old_row.get("pairs", 0))
        new_n = int(new_row.get("pairs", 0))
        if tag in BRIDGE_TAGS:
            medians[name] = statistics.fmean(
                (
                    float(old_row["median_residual_us"]),
                    float(new_row["median_residual_us"]) + correction_us,
                )
            )
            sources[name] = "bridge mean(S5a, corrected DISC)"
        elif new_n > old_n:
            if new_row["status"] != "OK":
                raise RuntimeError(f"no usable union value for {name}")
            medians[name] = float(new_row["median_residual_us"]) + correction_us
            sources[name] = "corrected DISC"
        else:
            if old_row["status"] != "OK":
                raise RuntimeError(f"no usable union value for {name}")
            medians[name] = float(old_row["median_residual_us"])
            sources[name] = "S5a"

    spread_us = max(medians.values()) - min(medians.values())
    result = {
        "preregistration": str(
            ROOT / "UWB_Part/logs/coldstart_20260730/G3_DISC_PREREGISTRATION.md"
        ),
        "windows": {
            "s5a": {"start_ns": old_start, "end_ns": old_end, "duration_s": old_duration},
            "disc": {"start_ns": new_start, "end_ns": new_end, "duration_s": new_duration},
        },
        "coverage": {
            "old_lowest_tag_vote": dict(lowest_old),
            "new_lowest_tag_vote": dict(lowest_new),
            "verdict": coverage_verdict,
        },
        "observer_selection": {
            "criterion": "maximize minimum union pair support; tie by total support",
            "chosen_snr": chosen,
            "minimum_support": min_support,
            "total_support": total_support,
            "all_candidates": [
                {"snr": snr, "minimum_support": low, "total_support": total}
                for low, total, snr in sorted(candidates, reverse=True)
            ],
        },
        "bridge_correction": {
            "tags": list(BRIDGE_TAGS),
            "per_tag_old_minus_new_us": bridge_deltas,
            "median_correction_us": correction_us,
            "disagreement_p2p_us": max(bridge_deltas) - min(bridge_deltas),
        },
        "g3_union": {
            "median_residual_us": medians,
            "source": sources,
            "spread_us": spread_us,
            "gate_us": 1000.0,
            "pass": spread_us <= 1000.0 and min_support >= 10,
        },
        "per_observer": per_observer,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "coverage_verdict": coverage_verdict,
                "chosen_observer": chosen,
                "minimum_support": min_support,
                "spread_us": spread_us,
                "g3_pass": result["g3_union"]["pass"],
            },
            indent=2,
        )
    )
    return 0 if result["g3_union"]["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
