#!/usr/bin/env python3
"""Fit an Action00 prefix and score node-root agreement on the untouched suffix."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from biospur_fusion.c2_uwb_root_world.action00_session_offsets import (
    corrected_epoch_consensus,
    fit_static_session_offsets,
)


ROOT = Path(__file__).resolve().parents[1]


def _quantiles(values) -> dict:
    array = np.asarray(values, dtype=float)
    return {
        "count": int(len(array)),
        "median_m": float(np.median(array)),
        "p90_m": float(np.quantile(array, 0.90)),
        "p95_m": float(np.quantile(array, 0.95)),
        "maximum_m": float(np.max(array)),
    }


def run(facts_path: Path, output: Path, training_duration_s: float = 8.0) -> dict:
    facts_path = facts_path.resolve()
    output = output.resolve()
    if output.exists() or ROOT not in output.parents:
        raise ValueError("output must be a new directory under Fusion_Part")
    rows = [json.loads(line) for line in facts_path.read_text().splitlines() if line]
    if not rows:
        raise ValueError("empty Action00 fact table")
    training_start = float(rows[0]["reference_time_s"])
    training_stop = training_start + float(training_duration_s)
    samples = [
        (
            float(row["reference_time_s"]), node["node"],
            node["candidate_root_world_m"],
        )
        for row in rows for node in row["nodes"] if node["direct_usable"]
    ]
    profile = fit_static_session_offsets(
        samples, training_start_s=training_start, training_stop_s=training_stop,
        minimum_samples=30,
        provenance=(
            "ACTION00_FIRST_8S_SESSION_RELATIVE_NODE_ROOT_DISAGREEMENT;"
            "NOT_ANTENNA_PHASE_CENTRE_OR_PER_ANCHOR_BIAS"
        ),
    )

    raw_spread = []
    corrected_spread = []
    raw_centres = []
    corrected_centres = []
    heldout_rows = 0
    heldout_path = output / "HELDOUT_EPOCHS.jsonl"
    output.mkdir(parents=False)
    with heldout_path.open("w", encoding="utf-8") as stream:
        for row in rows:
            if float(row["reference_time_s"]) < training_stop:
                continue
            candidates = {
                node["node"]: np.asarray(node["candidate_root_world_m"], dtype=float)
                for node in row["nodes"] if node["direct_usable"]
                and node["node"] in profile.offsets_by_node_m
            }
            if len(candidates) < 4:
                continue
            raw_centre = np.median(np.stack(list(candidates.values())), axis=0)
            corrected_centre, corrected = corrected_epoch_consensus(profile, candidates)
            raw_distances = {
                node: float(np.linalg.norm(value - raw_centre))
                for node, value in candidates.items()
            }
            corrected_distances = {
                node: float(np.linalg.norm(value - corrected_centre))
                for node, value in corrected.items()
            }
            raw_spread.extend(raw_distances.values())
            corrected_spread.extend(corrected_distances.values())
            raw_centres.append(raw_centre)
            corrected_centres.append(corrected_centre)
            stream.write(json.dumps({
                "epoch_sequence": row["epoch_sequence"],
                "reference_time_s": row["reference_time_s"],
                "nodes": sorted(candidates),
                "raw_consensus_root_m": raw_centre.tolist(),
                "corrected_consensus_root_m": corrected_centre.tolist(),
                "raw_node_distance_m": raw_distances,
                "corrected_node_distance_m": corrected_distances,
            }, sort_keys=True, separators=(",", ":")) + "\n")
            heldout_rows += 1
    if not heldout_rows:
        raise RuntimeError("no heldout epoch retained")
    raw_metrics = _quantiles(raw_spread)
    corrected_metrics = _quantiles(corrected_spread)
    raw_centre_motion = np.linalg.norm(
        np.asarray(raw_centres) - np.asarray(raw_centres)[0], axis=1,
    )
    corrected_centre_motion = np.linalg.norm(
        np.asarray(corrected_centres) - np.asarray(corrected_centres)[0], axis=1,
    )
    result = {
        "schema": "biospur.c2.action00.session_offsets.heldout.v1",
        "status": "HELDOUT_DIAGNOSTIC_COMPLETE",
        "product_ready": False,
        "scientific_pass": False,
        "facts_path": str(facts_path),
        "training_duration_s": training_duration_s,
        "training_start_s": training_start,
        "training_stop_s_exclusive": training_stop,
        "heldout_epoch_count": heldout_rows,
        "profile": {
            "digest": profile.digest,
            "common_root_gauge_m": profile.common_root_gauge_m.tolist(),
            "samples_by_node": dict(profile.samples_by_node),
            "offsets_by_node_m": {
                node: value.tolist()
                for node, value in sorted(profile.offsets_by_node_m.items())
            },
            "offset_norm_m": {
                node: float(np.linalg.norm(value))
                for node, value in sorted(profile.offsets_by_node_m.items())
            },
            "provenance": profile.provenance,
        },
        "heldout_raw_node_spread": raw_metrics,
        "heldout_corrected_node_spread": corrected_metrics,
        "median_spread_reduction_fraction": (
            1.0 - corrected_metrics["median_m"] / raw_metrics["median_m"]
        ),
        "heldout_consensus_motion_from_first": {
            "raw_endpoint_m": float(raw_centre_motion[-1]),
            "raw_maximum_m": float(raw_centre_motion.max()),
            "corrected_endpoint_m": float(corrected_centre_motion[-1]),
            "corrected_maximum_m": float(corrected_centre_motion.max()),
        },
    }
    (output / "RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-duration-s", type=float, default=8.0)
    arguments = parser.parse_args()
    print(json.dumps(run(
        arguments.facts, arguments.output, arguments.training_duration_s,
    ), indent=2, sort_keys=True))
