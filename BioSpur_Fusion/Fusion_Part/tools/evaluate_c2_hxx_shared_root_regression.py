#!/usr/bin/env python3
"""No-refit H01/H02 regression of the qualified C2 shared-root mechanism."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import time
from typing import Any

import numpy as np

from biospur_fusion.c2_3a_kinematics import (
    load_frozen_c2_3a,
    load_frozen_c2_hxx_diagnostics,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    FrozenHoldoutBodyProxy,
    NODE_TO_PROXY_POINT,
    frozen_world_alignment,
)
from biospur_fusion.c2_uwb_calibration.pair_bias import load_pair_bias_table
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    DATASET,
    _beacon_boundary_bridges,
    _clock_models,
    labelled_bounds_global_ns,
)
from biospur_fusion.c2_uwb_root_world.u0 import decode_uwb_only

from evaluate_c2_pair_bias_gate import (
    EPOCH_NS,
    _blind_test,
    _gate,
    _global_ns,
    _load_layout,
    _sha256,
    _write_json,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLOCK = (
    ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/"
    "CLOCK_TABLE_CALIBRATION_ONLY.json"
)
DEFAULT_BIAS_TABLE = (
    ROOT / "logs/c2_uwb_pair_bias_blind_gate_20260903_191022/"
    "PAIR_BIAS_TABLE.json"
)
HOLDOUTS = ("H01_boxing", "H02_golf")
CANDIDATES = ("fixed_bias_only", "bounded_bias_variance")


def _load_holdout(
    episode: str,
    clocks: dict[str, Any],
    bridges: list[tuple[float, float]],
) -> dict[str, Any]:
    repetition = DATASET / "holdout" / episode / "rep_01"
    raw = repetition / "raw/fusion_host_raw.cobs.bin"
    events = repetition / "events/ACTION_EVENTS.jsonl"
    rows, decode = decode_uwb_only(raw)
    lo, hi = labelled_bounds_global_ns(events, bridges)
    retained = [
        row for row in rows
        if row.node in clocks and lo <= _global_ns(row, clocks) < hi
    ]
    grouped: dict[int, list[Any]] = defaultdict(list)
    for row in retained:
        grouped[int(round(_global_ns(row, clocks) / EPOCH_NS))].append(row)
    groups = [
        sorted(values, key=lambda row: row.node)
        for _, values in sorted(grouped.items())
        if len(values) == len(NODE_TO_PROXY_POINT)
        and len({row.node for row in values}) == len(NODE_TO_PROXY_POINT)
    ]
    if len(groups) < 10:
        raise RuntimeError(f"{episode} has fewer than ten complete body epochs")
    return {
        "episode": episode,
        "episode_key": episode,
        "raw": raw,
        "events": events,
        "lo": lo,
        "hi": hi,
        "rows": rows,
        "retained": retained,
        "groups": groups,
        "partial_groups": len(grouped) - len(groups),
        "decode_errors": decode.decode_errors,
    }


def _expanded_volume_audit(
    summary: dict[str, Any], anchors: np.ndarray, margin_m: float = 0.75
) -> dict[str, Any]:
    lower = np.min(anchors, axis=0) - margin_m
    upper = np.max(anchors, axis=0) + margin_m
    median = np.asarray(summary["root_median_m"], dtype=float)
    return {
        "margin_m": margin_m,
        "lower_m": lower.tolist(),
        "upper_m": upper.tolist(),
        "median_root_m": median.tolist(),
        "median_inside": bool(np.all((median >= lower) & (median <= upper))),
        "note": "diagnostic envelope only; not position ground truth",
    }


def run(
    output: Path,
    clock_path: Path,
    bias_table_path: Path,
    candidate_policy: str = "fixed_bias_only",
) -> dict[str, Any]:
    if candidate_policy not in CANDIDATES:
        raise ValueError("unsupported HXX range policy")
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    clocks = _clock_models(clock_path)
    bridges = _beacon_boundary_bridges(clock_path)
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    calibration = load_frozen_c2_3a()
    holdout = load_frozen_c2_hxx_diagnostics()
    body = FrozenHoldoutBodyProxy.create(holdout, calibration)
    alignment, frozen_forward = frozen_world_alignment(calibration)
    biases = load_pair_bias_table(
        bias_table_path, nodes=NODE_TO_PROXY_POINT, allow_shadow=True
    )
    room_initial = np.array([
        float(np.mean(anchors[:, 0])),
        float(np.mean(anchors[:, 1])),
        0.95,
    ])

    episodes: dict[str, Any] = {}
    all_pass = True
    raw_inputs = {}
    for episode_key in HOLDOUTS:
        episode = _load_holdout(episode_key, clocks, bridges)

        def proxy_at_fraction(
            _unused: Any,
            key: str,
            fraction: float,
            world_from_frozen: np.ndarray,
        ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], int]:
            return body.at_fraction(key, fraction, world_from_frozen)

        main, cv = _blind_test(
            episode,
            biases=biases,
            kinematics=holdout,
            alignment=alignment,
            anchors=anchors,
            delays=delays,
            tag_delay=tag_delay,
            layout_sigma=layout_sigma,
            clocks=clocks,
            room_initial=room_initial,
            policies=("raw_all", candidate_policy),
            cv_policies=("raw_all", candidate_policy),
            proxy_at_fraction=proxy_at_fraction,
        )
        gate = _gate(
            main,
            cv,
            candidate_policy=candidate_policy,
            dynamic_motion_gate=True,
        )
        envelope = _expanded_volume_audit(main[candidate_policy], anchors)
        gate["checks"]["median_root_inside_expanded_anchor_volume"] = envelope[
            "median_inside"
        ]
        gate["pass"] = all(gate["checks"].values())
        all_pass &= gate["pass"]
        episodes[episode_key] = {
            "input": {
                "complete_ten_node_epochs": len(episode["groups"]),
                "partial_groups_discarded": episode["partial_groups"],
                "decoded_rows": len(episode["rows"]),
                "retained_rows": len(episode["retained"]),
                "decode_errors": episode["decode_errors"],
            },
            "main": main,
            "held_node_cv": cv,
            "expanded_anchor_volume": envelope,
            "gate": gate,
        }
        raw_inputs[episode_key] = {
            "path": str(episode["raw"]),
            "sha256": _sha256(episode["raw"]),
            "events_path": str(episode["events"]),
            "events_sha256": _sha256(episode["events"]),
        }

    result = {
        "schema": "biospur-c2-hxx-shared-root-regression-v1",
        "status": (
            "HXX_SHARED_ROOT_REGRESSION_PASS"
            if all_pass else "HXX_SHARED_ROOT_REGRESSION_REJECT"
        ),
        "mechanism_regression_pass": all_pass,
        "scientific_pass": False,
        "holdout_status": "POST_CALIBRATION_REGRESSION_NOT_FRESH_WORLD_TRUTH",
        "candidate_policy": candidate_policy,
        "pair_bias_refit_on_hxx": False,
        "body_proxy_refit_on_hxx": False,
        "action_semantics_used": False,
        "clock_contract": "BEACON_LBD_GLOBAL_TDMA_PLUS_NODE_B306_TIMER2",
        "uwb_cadence_hz": 1000.0 / 120.0,
        "body_proxy_contract": (
            "SAME_TEN_FROZEN_NAMED_DISPLAY_PROXY_POINTS_AS_00_TO_19;"
            "NOT_ANTENNA_PHASE_CENTRES"
        ),
        "alignment": {
            "source": "FROZEN_00_INITIAL_HEADING_ONLY",
            "target_forward_v4": [0.0, -1.0, 0.0],
            "frozen_forward": frozen_forward.tolist(),
            "determinant": float(np.linalg.det(alignment)),
        },
        "episodes": episodes,
        "wall_s": time.perf_counter() - started,
    }
    _write_json(output / "RESULT.json", result)
    lines = [
        "# C2 H01/H02 shared-root no-refit regression",
        "",
        f"Status: **{result['status']}** (scientific pass remains false).",
        "",
        "The 00 pair table, Beacon/TIMER2 clock, alignment, and ten named proxy points were reused without holdout refit.",
        "",
        "| Episode | epochs | root step change | held median change | held p95 change | gate |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for key, row in episodes.items():
        raw_main = row["main"]["raw_all"]
        candidate_main = row["main"][candidate_policy]
        raw_cv = row["held_node_cv"]["raw_all"]
        candidate_cv = row["held_node_cv"][candidate_policy]
        change = lambda value, base: 100.0 * (value / base - 1.0)
        lines.append(
            f"| {key} | {row['input']['complete_ten_node_epochs']} | "
            f"{change(candidate_main['root_step_p95_m'], raw_main['root_step_p95_m']):.3f}% | "
            f"{change(candidate_cv['median_abs_held_range_residual_m'], raw_cv['median_abs_held_range_residual_m']):.3f}% | "
            f"{change(candidate_cv['p95_abs_held_range_residual_m'], raw_cv['p95_abs_held_range_residual_m']):.3f}% | "
            f"{'PASS' if row['gate']['pass'] else 'REJECT'} |"
        )
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")
    _write_json(output / "EVIDENCE_MANIFEST.json", {
        "tool": str(Path(__file__).resolve()),
        "tool_sha256": _sha256(Path(__file__).resolve()),
        "clock": str(clock_path),
        "clock_sha256": _sha256(clock_path),
        "pair_bias_table": str(bias_table_path),
        "pair_bias_table_sha256": _sha256(bias_table_path),
        "raw_inputs": raw_inputs,
        "frozen_hxx_source": holdout.source_artifact,
        "frozen_hxx_source_sha256": holdout.source_sha256,
    })
    sealed = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    (output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in sealed)
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock", type=Path, default=DEFAULT_CLOCK)
    parser.add_argument("--bias-table", type=Path, default=DEFAULT_BIAS_TABLE)
    parser.add_argument("--candidate-policy", choices=CANDIDATES, default="fixed_bias_only")
    args = parser.parse_args()
    print(json.dumps(run(
        args.output.resolve(),
        args.clock.resolve(),
        args.bias_table.resolve(),
        args.candidate_policy,
    ), indent=2))


if __name__ == "__main__":
    main()
