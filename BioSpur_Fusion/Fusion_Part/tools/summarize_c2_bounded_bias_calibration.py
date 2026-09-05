#!/usr/bin/env python3
"""Seal the complete C2 02--19 bounded-bias mechanism qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from biospur_fusion.c2_uwb_root_world.calibration import CALIBRATION_ORDER


EXPECTED_EPISODES = set(CALIBRATION_ORDER) - {"00_initial_still"}
EXPECTED_CANDIDATE = "bounded_bias_variance"
EXPECTED_CLOCK = "BEACON_LBD_GLOBAL_TDMA_PLUS_NODE_B306_TIMER2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_seal(directory: Path) -> dict[str, str]:
    seal = directory / "SHA256SUMS"
    entries = {}
    for line in seal.read_text().splitlines():
        digest, name = line.split("  ", 1)
        path = directory / name
        if not path.is_file() or _sha256(path) != digest:
            raise ValueError(f"seal mismatch: {path}")
        entries[name] = digest
    if "RESULT.json" not in entries:
        raise ValueError(f"sealed result missing: {directory}")
    return entries


def _change(candidate: float, baseline: float) -> float:
    return float(candidate / baseline - 1.0)


def aggregate(result_paths: list[Path], bias_table: Path) -> dict[str, Any]:
    expected_bias_sha = _sha256(bias_table)
    episodes = {}
    manifests = {}
    for result_path in result_paths:
        result_path = result_path.resolve()
        directory = result_path.parent
        seal = _verify_seal(directory)
        result = json.loads(result_path.read_text())
        manifest = json.loads((directory / "EVIDENCE_MANIFEST.json").read_text())
        episode = str(result["blind_episode"])
        if episode in episodes:
            raise ValueError(f"duplicate episode result: {episode}")
        checks = {
            "candidate": result.get("candidate_policy") == EXPECTED_CANDIDATE,
            "bias_reused": result.get("bias_table_reused_without_refit") is True,
            "clock": result.get("clock_contract") == EXPECTED_CLOCK,
            "gate": result.get("preregistered_gate", {}).get("pass") is True,
            "scientific_not_overclaimed": result.get("scientific_pass") is False,
            "bias_sha": manifest.get("pair_bias_table_sha256") == expected_bias_sha,
            "result_sha": seal["RESULT.json"] == _sha256(result_path),
        }
        if not all(checks.values()):
            failed = [name for name, passed in checks.items() if not passed]
            raise ValueError(f"{episode} provenance/gate failure: {failed}")
        raw = result["blind_main"]["raw_all"]
        candidate = result["blind_main"][EXPECTED_CANDIDATE]
        raw_cv = result["blind_held_node_cv"]["raw_all"]
        candidate_cv = result["blind_held_node_cv"][EXPECTED_CANDIDATE]
        episodes[episode] = {
            "result_path": str(result_path),
            "result_sha256": seal["RESULT.json"],
            "complete_epochs": int(candidate["expected_epochs"]),
            "accepted_epochs": int(candidate["accepted_epochs"]),
            "held_node_solves": int(candidate_cv["expected_solves"]),
            "accepted_held_node_solves": int(candidate_cv["accepted_solves"]),
            "root_step_p95_change_fraction": _change(
                candidate["root_step_p95_m"], raw["root_step_p95_m"]
            ),
            "held_median_change_fraction": _change(
                candidate_cv["median_abs_held_range_residual_m"],
                raw_cv["median_abs_held_range_residual_m"],
            ),
            "held_p95_change_fraction": _change(
                candidate_cv["p95_abs_held_range_residual_m"],
                raw_cv["p95_abs_held_range_residual_m"],
            ),
        }
        manifests[episode] = {
            "manifest_path": str(directory / "EVIDENCE_MANIFEST.json"),
            "manifest_sha256": seal["EVIDENCE_MANIFEST.json"],
        }
    if set(episodes) != EXPECTED_EPISODES:
        missing = sorted(EXPECTED_EPISODES - set(episodes))
        extra = sorted(set(episodes) - EXPECTED_EPISODES)
        raise ValueError(f"episode coverage mismatch; missing={missing}, extra={extra}")

    metric_names = (
        "root_step_p95_change_fraction",
        "held_median_change_fraction",
        "held_p95_change_fraction",
    )
    distributions = {}
    for metric in metric_names:
        values = np.asarray([row[metric] for row in episodes.values()], dtype=float)
        worst_episode = max(episodes, key=lambda episode: episodes[episode][metric])
        best_episode = min(episodes, key=lambda episode: episodes[episode][metric])
        distributions[metric] = {
            "minimum": float(np.min(values)),
            "median": float(np.median(values)),
            "maximum": float(np.max(values)),
            "best_episode": best_episode,
            "worst_episode": worst_episode,
            "non_worse_episode_count": int(np.sum(values <= 0.0)),
        }
    all_pass = all(
        row["accepted_epochs"] == row["complete_epochs"]
        and row["accepted_held_node_solves"] == row["held_node_solves"]
        and row["root_step_p95_change_fraction"] <= 0.0
        and row["held_median_change_fraction"] <= 0.0
        and row["held_p95_change_fraction"] <= 0.0
        for row in episodes.values()
    )
    return {
        "schema": "biospur-c2-bounded-bias-calibration-aggregate-v1",
        "status": "MECHANISM_QUALIFIED_02_TO_19" if all_pass else "MECHANISM_REJECTED",
        "mechanism_qualification_pass": all_pass,
        "scientific_pass": False,
        "candidate": EXPECTED_CANDIDATE,
        "calibration_owner": "00_initial_still_held-node_pair_residuals",
        "validated_episode_count": len(episodes),
        "validated_episode_set": sorted(episodes),
        "total_complete_epochs": sum(row["complete_epochs"] for row in episodes.values()),
        "total_accepted_epochs": sum(row["accepted_epochs"] for row in episodes.values()),
        "total_held_node_solves": sum(row["held_node_solves"] for row in episodes.values()),
        "total_accepted_held_node_solves": sum(
            row["accepted_held_node_solves"] for row in episodes.values()
        ),
        "bias_table": str(bias_table.resolve()),
        "bias_table_sha256": expected_bias_sha,
        "clock_contract": EXPECTED_CLOCK,
        "distributions": distributions,
        "episodes": episodes,
        "input_manifests": manifests,
        "boundary": (
            "QUALIFIES_RANGE_PREPROCESSING_AND_SHARED_ROOT_MECHANISM_ONLY;_"
            "NO_WORLD_GROUND_TRUTH,_ANTENNA_PHASE_CENTRES,_H01_H02,_OR_IMU_ROOT_FUSION"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, action="append", required=True)
    parser.add_argument("--bias-table", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    summary = aggregate(args.result, args.bias_table.resolve())
    result_path = output / "AGGREGATE.json"
    result_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    lines = [
        "# C2 bounded-bias calibration aggregate", "",
        f"Status: **{summary['status']}**; scientific pass remains false.", "",
        f"Validated {summary['validated_episode_count']} episodes, "
        f"{summary['total_accepted_epochs']}/{summary['total_complete_epochs']} epochs, and "
        f"{summary['total_accepted_held_node_solves']}/{summary['total_held_node_solves']} held-node solves.",
        "", "| Metric change vs raw-all | best | median | worst | worst episode |", "|---|---:|---:|---:|---|",
    ]
    for metric, row in summary["distributions"].items():
        lines.append(
            f"| {metric} | {row['minimum']:.3%} | {row['median']:.3%} | "
            f"{row['maximum']:.3%} | {row['worst_episode']} |"
        )
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")
    manifest = {
        "tool": str(Path(__file__).resolve()),
        "tool_sha256": _sha256(Path(__file__).resolve()),
        "aggregate_sha256": _sha256(result_path),
        "bias_table_sha256": summary["bias_table_sha256"],
        "input_result_count": len(args.result),
    }
    (output / "EVIDENCE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n"
    )
    sealed = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    (output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in sealed)
    )
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
