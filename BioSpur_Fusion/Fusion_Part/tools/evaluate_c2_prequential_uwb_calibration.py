#!/usr/bin/env python3
"""Causal 00--19 UWB pair-health calibration with held-node scoring.

For episode k, the candidate table contains only held-node statistics from
episodes < k. The current episode is appended only after the frozen-00
baseline and causal candidate have been scored on the same epoch subset.
This shadow study never mutates the frozen Capture2 trajectory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    NODE_TO_PROXY_POINT,
    frozen_world_alignment,
)
from biospur_fusion.c2_uwb_calibration.pair_bias import (
    PairBiasEstimate,
    aggregate_causal_pair_bias_tables,
    propagate_causal_pair_uncertainty,
)
from biospur_fusion.c2_uwb_root_world.calibration import CALIBRATION_ORDER
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    _beacon_boundary_bridges,
    _clock_models,
)

from evaluate_c2_hxx_shared_root_regression import DEFAULT_CLOCK
from evaluate_c2_pair_bias_gate import (
    _blind_test,
    _fit_biases,
    _load_episode,
    _load_layout,
)


POLICY = "bounded_bias_variance"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _table_rows(table: dict[tuple[str, int], PairBiasEstimate]) -> list[dict]:
    return [
        {
            "node": item.node,
            "anchor": item.anchor,
            "bias_m": item.bias_m,
            "robust_sigma_m": item.robust_sigma_m,
            "sample_count": item.sample_count,
        }
        for _, item in sorted(table.items())
    ]


def _subsample(episode: dict, stride: int) -> dict:
    return {**episode, "groups": episode["groups"][::stride]}


def _score(
    episode: dict,
    table: dict[tuple[str, int], PairBiasEstimate],
    *,
    kinematics,
    alignment: np.ndarray,
    anchors: np.ndarray,
    delays: np.ndarray,
    tag_delay: float,
    layout_sigma: float,
    clocks: dict,
    room_initial: np.ndarray,
) -> dict:
    main, cv = _blind_test(
        episode,
        biases=table,
        kinematics=kinematics,
        alignment=alignment,
        anchors=anchors,
        delays=delays,
        tag_delay=tag_delay,
        layout_sigma=layout_sigma,
        clocks=clocks,
        room_initial=room_initial,
        policies=("raw_all", POLICY),
        cv_policies=(POLICY,),
    )
    return {"main": main[POLICY], "held_node": cv[POLICY]}


def run(
    output: Path,
    *,
    clock_path: Path,
    stride: int = 4,
    propagation: str = "uncertainty_only",
) -> dict:
    if stride < 1:
        raise ValueError("stride must be positive")
    if propagation not in {"bias_and_uncertainty", "uncertainty_only"}:
        raise ValueError("unsupported causal propagation mode")
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    clocks = _clock_models(clock_path)
    bridges = _beacon_boundary_bridges(clock_path)
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    kinematics = load_frozen_c2_3a()
    alignment, _ = frozen_world_alignment(kinematics)
    room_initial = np.array([
        float(np.mean(anchors[:, 0])),
        float(np.mean(anchors[:, 1])),
        0.95,
    ])

    episodes = {
        action: _subsample(_load_episode(action, clocks, bridges), stride)
        for action in CALIBRATION_ORDER
    }
    initial, initial_audit = _fit_biases(
        episodes[CALIBRATION_ORDER[0]],
        kinematics=kinematics,
        alignment=alignment,
        anchors=anchors,
        delays=delays,
        tag_delay=tag_delay,
        layout_sigma=layout_sigma,
        clocks=clocks,
        room_initial=room_initial,
    )
    history = [initial]
    rows = []
    for action in CALIBRATION_ORDER[1:]:
        candidate = (
            aggregate_causal_pair_bias_tables(history, nodes=NODE_TO_PROXY_POINT)
            if propagation == "bias_and_uncertainty"
            else propagate_causal_pair_uncertainty(
                initial, history, nodes=NODE_TO_PROXY_POINT
            )
        )
        episode = episodes[action]
        common = {
            "kinematics": kinematics,
            "alignment": alignment,
            "anchors": anchors,
            "delays": delays,
            "tag_delay": tag_delay,
            "layout_sigma": layout_sigma,
            "clocks": clocks,
            "room_initial": room_initial,
        }
        baseline_score = _score(episode, initial, **common)
        candidate_score = _score(episode, candidate, **common)
        base_cv = baseline_score["held_node"]
        causal_cv = candidate_score["held_node"]
        rows.append({
            "episode": action,
            "prior_episode_count": len(history),
            "same_episode_refit": False,
            "sampled_complete_epochs": len(episode["groups"]),
            "frozen_00": baseline_score,
            "causal_prior": candidate_score,
            "held_median_change_fraction": (
                causal_cv["median_abs_held_range_residual_m"]
                / base_cv["median_abs_held_range_residual_m"] - 1.0
            ),
            "held_p95_change_fraction": (
                causal_cv["p95_abs_held_range_residual_m"]
                / base_cv["p95_abs_held_range_residual_m"] - 1.0
            ),
        })
        current, _ = _fit_biases(episode, **common)
        history.append(current)

    final_table = (
        aggregate_causal_pair_bias_tables(history, nodes=NODE_TO_PROXY_POINT)
        if propagation == "bias_and_uncertainty"
        else propagate_causal_pair_uncertainty(
            initial, history, nodes=NODE_TO_PROXY_POINT
        )
    )
    base_median = np.asarray([
        row["frozen_00"]["held_node"]["median_abs_held_range_residual_m"]
        for row in rows
    ])
    causal_median = np.asarray([
        row["causal_prior"]["held_node"]["median_abs_held_range_residual_m"]
        for row in rows
    ])
    base_p95 = np.asarray([
        row["frozen_00"]["held_node"]["p95_abs_held_range_residual_m"]
        for row in rows
    ])
    causal_p95 = np.asarray([
        row["causal_prior"]["held_node"]["p95_abs_held_range_residual_m"]
        for row in rows
    ])
    checks = {
        "all_episodes_scored_before_ingest": len(rows) == len(CALIBRATION_ORDER) - 1,
        "aggregate_held_median_noninferior": float(np.median(causal_median))
        <= float(np.median(base_median)),
        "aggregate_held_p95_noninferior": float(np.median(causal_p95))
        <= float(np.median(base_p95)),
        "at_least_half_episodes_improve_median": float(np.mean(
            causal_median <= base_median
        )) >= 0.5,
    }
    result = {
        "schema": "biospur.c2.prequential_uwb_calibration.v1",
        "status": "PASS" if all(checks.values()) else "REJECT",
        "scientific_pass": False,
        "clock_contract": "BEACON_LBD_GLOBAL_PLUS_B306_TIMER2_STROBE_TROUND_HALF",
        "episode_order": list(CALIBRATION_ORDER),
        "epoch_stride": stride,
        "propagation": propagation,
        "initial_00_audit": initial_audit,
        "prequential_checks": checks,
        "aggregate": {
            "frozen_00_median_of_episode_held_median_abs_m": float(np.median(base_median)),
            "causal_median_of_episode_held_median_abs_m": float(np.median(causal_median)),
            "median_change_fraction": float(
                np.median(causal_median) / np.median(base_median) - 1.0
            ),
            "frozen_00_median_of_episode_held_p95_abs_m": float(np.median(base_p95)),
            "causal_median_of_episode_held_p95_abs_m": float(np.median(causal_p95)),
            "p95_change_fraction": float(
                np.median(causal_p95) / np.median(base_p95) - 1.0
            ),
            "episode_median_improvement_fraction": float(np.mean(
                causal_median <= base_median
            )),
        },
        "rows": rows,
        "boundary": (
            "HELD_NODE_INTERNAL_PREDICTION_ONLY;DISPLAY_PROXY_NOT_PHASE_CENTRES;"
            "NO_HXX_AND_NO_EXTERNAL_POSITION_TRUTH"
        ),
        "wall_s": time.perf_counter() - started,
    }
    (output / "RESULT.json").write_text(json.dumps(result, indent=2) + "\n")
    (output / "FINAL_CAUSAL_PAIR_TABLE.json").write_text(json.dumps({
        "schema": "biospur-c2-held-node-pair-bias-v1",
        "source_episode": "00_to_19_prequential_after_scoring",
        "method": (
            "episode_balanced_median_with_between_episode_dispersion"
            if propagation == "bias_and_uncertainty"
            else "fixed_00_bias_plus_causal_cross_episode_uncertainty"
        ),
        "shadow_only": True,
        "calibration_internal_gate_pass": result["status"] == "PASS",
        "estimates": _table_rows(final_table),
    }, indent=2) + "\n")
    manifest = {
        "tool": str(Path(__file__).resolve()),
        "tool_sha256": _sha256(Path(__file__).resolve()),
        "clock": str(clock_path),
        "clock_sha256": _sha256(clock_path),
        "raw_inputs": {
            action: {
                "path": str(episodes[action]["raw"]),
                "sha256": _sha256(episodes[action]["raw"]),
            }
            for action in CALIBRATION_ORDER
        },
    }
    (output / "EVIDENCE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    sealed = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    (output / "SHA256SUMS").write_text("".join(
        f"{_sha256(path)}  {path.name}\n" for path in sealed
    ))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock", type=Path, default=DEFAULT_CLOCK)
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument(
        "--propagation",
        choices=("uncertainty_only", "bias_and_uncertainty"),
        default="uncertainty_only",
    )
    args = parser.parse_args()
    result = run(
        args.output.resolve(), clock_path=args.clock.resolve(), stride=args.stride,
        propagation=args.propagation,
    )
    print(json.dumps({
        "status": result["status"],
        "scientific_pass": result["scientific_pass"],
        "aggregate": result["aggregate"],
        "checks": result["prequential_checks"],
        "wall_s": result["wall_s"],
    }, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
