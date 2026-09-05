#!/usr/bin/env python3
"""Evaluate 00+17 still-state UWB calibration on H01/H02.

The 17 table is estimated with held-node roots. H01/H02 are scored without
refit and on identical epoch subsets against the frozen 00 table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

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
from biospur_fusion.c2_uwb_calibration.pair_bias import (
    aggregate_causal_pair_bias_tables,
    load_pair_bias_table,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    _beacon_boundary_bridges,
    _clock_models,
)

from evaluate_c2_hxx_shared_root_regression import (
    DEFAULT_BIAS_TABLE,
    DEFAULT_CLOCK,
    HOLDOUTS,
    _load_holdout,
)
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


def _subsample(episode: dict, stride: int) -> dict:
    return {**episode, "groups": episode["groups"][::stride]}


def run(
    output: Path,
    *,
    clock_path: Path,
    bias_table_path: Path,
    stride: int = 4,
) -> dict:
    if stride < 1:
        raise ValueError("stride must be positive")
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    clocks = _clock_models(clock_path)
    bridges = _beacon_boundary_bridges(clock_path)
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    calibration = load_frozen_c2_3a()
    diagnostics = load_frozen_c2_hxx_diagnostics()
    body = FrozenHoldoutBodyProxy.create(diagnostics, calibration)
    alignment, _ = frozen_world_alignment(calibration)
    room_initial = np.array([
        float(np.mean(anchors[:, 0])),
        float(np.mean(anchors[:, 1])),
        0.95,
    ])
    initial = load_pair_bias_table(
        bias_table_path, nodes=NODE_TO_PROXY_POINT
    )
    final_still = _subsample(
        _load_episode("17_final_still", clocks, bridges), stride
    )
    common = {
        "kinematics": calibration,
        "alignment": alignment,
        "anchors": anchors,
        "delays": delays,
        "tag_delay": tag_delay,
        "layout_sigma": layout_sigma,
        "clocks": clocks,
        "room_initial": room_initial,
    }
    final_estimates, final_audit = _fit_biases(final_still, **common)
    combined = aggregate_causal_pair_bias_tables(
        [initial, final_estimates], nodes=NODE_TO_PROXY_POINT
    )

    def proxy(_unused, action, fraction, world_from_frozen):
        return body.at_fraction(action, fraction, world_from_frozen)

    rows = []
    for action in HOLDOUTS:
        episode = _subsample(_load_holdout(action, clocks, bridges), stride)
        scores = {}
        for name, table in (("frozen_00", initial), ("still_00_17", combined)):
            main, cv = _blind_test(
                episode,
                biases=table,
                kinematics=diagnostics,
                alignment=alignment,
                anchors=anchors,
                delays=delays,
                tag_delay=tag_delay,
                layout_sigma=layout_sigma,
                clocks=clocks,
                room_initial=room_initial,
                policies=("raw_all", POLICY),
                cv_policies=(POLICY,),
                proxy_at_fraction=proxy,
            )
            scores[name] = {"main": main[POLICY], "held_node": cv[POLICY]}
        base = scores["frozen_00"]["held_node"]
        candidate = scores["still_00_17"]["held_node"]
        rows.append({
            "action": action,
            "sampled_complete_epochs": len(episode["groups"]),
            "holdout_refit": False,
            "scores": scores,
            "held_median_change_fraction": (
                candidate["median_abs_held_range_residual_m"]
                / base["median_abs_held_range_residual_m"] - 1.0
            ),
            "held_p95_change_fraction": (
                candidate["p95_abs_held_range_residual_m"]
                / base["p95_abs_held_range_residual_m"] - 1.0
            ),
        })
    checks = {
        "both_holdouts_median_noninferior": all(
            row["held_median_change_fraction"] <= 0.0 for row in rows
        ),
        "both_holdouts_p95_noninferior": all(
            row["held_p95_change_fraction"] <= 0.0 for row in rows
        ),
        "no_holdout_refit": all(not row["holdout_refit"] for row in rows),
    }
    passed = all(checks.values())
    result = {
        "schema": "biospur.c2.static_00_17_pair_calibration_hxx.v1",
        "status": "PASS" if passed else "REJECT",
        "scientific_pass": False,
        "epoch_stride": stride,
        "clock_contract": "BEACON_LBD_GLOBAL_PLUS_B306_TIMER2_STROBE_TROUND_HALF",
        "final_still_audit": final_audit,
        "checks": checks,
        "rows": rows,
        "promotion": "00_PLUS_17" if passed else "KEEP_FROZEN_00",
        "boundary": (
            "INTERNAL_HELD_NODE_SCORE;DISPLAY_PROXY_NOT_PHASE_CENTRES;"
            "NO_EXTERNAL_POSITION_TRUTH"
        ),
        "wall_s": time.perf_counter() - started,
    }
    (output / "RESULT.json").write_text(json.dumps(result, indent=2) + "\n")
    (output / "COMBINED_00_17_TABLE.json").write_text(json.dumps({
        "schema": "biospur-c2-held-node-pair-bias-v1",
        "source_episode": "00_initial_still+17_final_still",
        "method": "episode_balanced_median_with_between_episode_dispersion",
        "promoted": passed,
        "estimates": [
            {
                "node": item.node,
                "anchor": item.anchor,
                "bias_m": item.bias_m,
                "robust_sigma_m": item.robust_sigma_m,
                "sample_count": item.sample_count,
            }
            for _, item in sorted(combined.items())
        ],
    }, indent=2) + "\n")
    (output / "EVIDENCE_MANIFEST.json").write_text(json.dumps({
        "tool": str(Path(__file__).resolve()),
        "tool_sha256": _sha256(Path(__file__).resolve()),
        "clock": str(clock_path),
        "clock_sha256": _sha256(clock_path),
        "frozen_00_table": str(bias_table_path),
        "frozen_00_table_sha256": _sha256(bias_table_path),
        "final_still_raw": str(final_still["raw"]),
        "final_still_raw_sha256": _sha256(final_still["raw"]),
    }, indent=2) + "\n")
    files = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    (output / "SHA256SUMS").write_text("".join(
        f"{_sha256(path)}  {path.name}\n" for path in files
    ))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock", type=Path, default=DEFAULT_CLOCK)
    parser.add_argument("--bias-table", type=Path, default=DEFAULT_BIAS_TABLE)
    parser.add_argument("--stride", type=int, default=4)
    args = parser.parse_args()
    result = run(
        args.output.resolve(),
        clock_path=args.clock.resolve(),
        bias_table_path=args.bias_table.resolve(),
        stride=args.stride,
    )
    print(json.dumps({
        "status": result["status"],
        "promotion": result["promotion"],
        "checks": result["checks"],
        "rows": [
            {
                "action": row["action"],
                "held_median_change_fraction": row["held_median_change_fraction"],
                "held_p95_change_fraction": row["held_p95_change_fraction"],
            }
            for row in result["rows"]
        ],
        "wall_s": result["wall_s"],
    }, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
