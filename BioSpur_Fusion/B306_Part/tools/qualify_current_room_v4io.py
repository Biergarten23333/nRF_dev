#!/usr/bin/env python3
"""Qualify a fresh SW100 data set with the frozen production V4-io solver.

This module deliberately imports, rather than reimplements, the frozen solver.
It adds deterministic replay, split-sample, multistart, and leave-one sensitivity
diagnostics and writes a capture-ready relative layout.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np


REPO = Path(__file__).resolve().parents[2]
PRODUCTION = (
    REPO.parent / "BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/"
    "run_clean_full_compare.py"
)
ANCHORS = "ABCDEFGH"
THRESHOLDS = {
    "minimum_valid_samples_per_direction": 99,
    "maximum_full_pair_rms_mm": 100.0,
    "maximum_split_pair_distance_delta_mm": 150.0,
    "maximum_split_delay_delta_mm": 30.0,
    "maximum_multistart_pair_distance_delta_mm": 10.0,
    "deterministic_absolute_tolerance": 1e-9,
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def pair_vector(x: np.ndarray, ids: list[int]) -> dict[tuple[int, int], float]:
    return {
        (ids[i], ids[j]): float(np.linalg.norm(x[i] - x[j]))
        for i in range(len(ids)) for j in range(i + 1, len(ids))
    }


def maximum_common_delta(left: dict, right: dict) -> float:
    common = sorted(set(left) & set(right))
    return max((abs(left[key] - right[key]) for key in common), default=0.0)


def residual_rms(x: np.ndarray, delay: np.ndarray, fused: dict, ids: list[int]) -> float:
    local = {anchor: index for index, anchor in enumerate(ids)}
    values = []
    for (a, b), observed in fused.items():
        if a not in local or b not in local:
            continue
        i, j = local[a], local[b]
        predicted = np.linalg.norm(x[i] - x[j]) + delay[i] + delay[j]
        values.append(float(predicted - observed))
    return float(np.sqrt(np.mean(np.square(values))))


def solve_best(fc, mod, raw: dict, ids: list[int]):
    fused = fc.fuse_all(mod, raw, ids)["v3"]
    init, _ = mod.solve_autopos_v1(fused, ids)
    candidates = []
    for label, candidate_init in (("production_init", init), ("reflected_init", init * [1, 1, -1])):
        x, delay, result = mod.solve_v4(fused, ids, candidate_init)
        candidates.append((label, x, delay, result))
    successful = [item for item in candidates if bool(item[3].success)]
    if not successful:
        raise RuntimeError("V4-io failed for both production and reflected initialization")
    # The frozen objective includes a signed two-layer physical prior.  Picking
    # its lowest-cost converged basin is solver-defined mirror selection.
    selected = min(successful, key=lambda item: float(item[3].cost))
    return selected, candidates, fused, init


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    fc = load_module(PRODUCTION, "current_room_v4io_fc")
    fc.SWEEP_CSV = args.pairs.resolve()
    mod = fc.load_eval_module()
    ids = list(range(8))
    raw = fc.load_sweep_grouped()
    selected, candidates, fused, production_init = solve_best(fc, mod, raw, ids)
    selected_label, x, delay, result = selected
    baseline_pairs = pair_vector(x, ids)

    completeness = []
    for a in ids:
        for b in ids:
            if a == b:
                continue
            values = raw.get((a, b), [])
            completeness.append({
                "initiator": ANCHORS[a], "responder": ANCHORS[b],
                "valid_samples": len(values),
                "required_samples": THRESHOLDS["minimum_valid_samples_per_direction"],
                "pass": int(len(values) >= THRESHOLDS["minimum_valid_samples_per_direction"]),
            })
    write_csv(args.out_dir / "SW100_PAIR_COMPLETENESS.csv", completeness)

    pair_rows = []
    for a in ids:
        for b in range(a + 1, 8):
            observed = fused[(a, b)] if (a, b) in fused else fused[(b, a)]
            predicted = baseline_pairs[(a, b)] + delay[a] + delay[b]
            pair_rows.append({
                "pair": f"{ANCHORS[a]}-{ANCHORS[b]}",
                "forward_samples": len(raw.get((a, b), [])),
                "reverse_samples": len(raw.get((b, a), [])),
                "fused_v3_mm": observed,
                "geometry_distance_mm": baseline_pairs[(a, b)],
                "anchor_delay_sum_mm": delay[a] + delay[b],
                "predicted_mm": predicted,
                "residual_mm": predicted - observed,
            })
    write_csv(args.out_dir / "V4IO_PAIR_RESIDUALS.csv", pair_rows)

    first_raw = {key: values[: len(values) // 2] for key, values in raw.items()}
    last_raw = {key: values[len(values) // 2 :] for key, values in raw.items()}
    first, _, first_fused, _ = solve_best(fc, mod, first_raw, ids)
    last, _, last_fused, _ = solve_best(fc, mod, last_raw, ids)
    split_pair_delta = maximum_common_delta(pair_vector(first[1], ids), pair_vector(last[1], ids))
    split_delay_delta = float(np.max(np.abs(first[2] - last[2])))
    # Cross-half residuals are genuine held-out diagnostics.
    first_on_last = residual_rms(first[1], first[2], last_fused, ids)
    last_on_first = residual_rms(last[1], last[2], first_fused, ids)

    multistart_rows = []
    multistart_max = 0.0
    starts = [("production_init", production_init), ("reflected_init", production_init * [1, 1, -1])]
    for seed in range(4):
        rng = np.random.default_rng(seed)
        starts.append((f"perturbed_{seed}", mod.gauge_align_local(production_init + rng.normal(0, 200, production_init.shape))))
    for label, init in starts:
        sx, sd, sr = mod.solve_v4(fused, ids, init)
        delta = maximum_common_delta(baseline_pairs, pair_vector(sx, ids))
        multistart_max = max(multistart_max, delta)
        multistart_rows.append({
            "initialization": label, "success": int(bool(sr.success)), "cost": float(sr.cost),
            "max_pair_distance_delta_from_selected_mm": delta,
            "max_delay_delta_from_selected_mm": float(np.max(np.abs(sd - delay))),
            "layer_gap_median_mm": sr.physical_diagnostics.get("layer_gap_median_mm"),
        })
    write_csv(args.out_dir / "V4IO_MULTISTART.csv", multistart_rows)

    leave_pair_rows = []
    leave_pair_max = 0.0
    for omitted in sorted(fused):
        reduced = dict(fused)
        reduced.pop(omitted)
        # The frozen MDS initializer represents a missing edge as zero distance;
        # it is therefore not a valid leave-one initializer.  Sensitivity is a
        # local deletion test and intentionally starts at the full-data optimum.
        lx, ld, lr = mod.solve_v4(reduced, ids, x)
        delta = maximum_common_delta(baseline_pairs, pair_vector(lx, ids))
        leave_pair_max = max(leave_pair_max, delta)
        leave_pair_rows.append({
            "omitted_pair": f"{ANCHORS[omitted[0]]}-{ANCHORS[omitted[1]]}",
            "success": int(bool(lr.success)), "cost": float(lr.cost),
            "max_pair_distance_delta_mm": delta,
            "max_delay_delta_mm": float(np.max(np.abs(ld - delay))),
        })
    write_csv(args.out_dir / "V4IO_LEAVE_ONE_PAIR.csv", leave_pair_rows)

    leave_anchor_rows = []
    leave_anchor_max = 0.0
    for omitted in ids:
        kept = [anchor for anchor in ids if anchor != omitted]
        reduced_fused = {key: value for key, value in fused.items() if omitted not in key}
        reduced_init = mod.gauge_align_local(x[kept])
        rx, rd, rr = mod.solve_v4(reduced_fused, kept, reduced_init)
        delta = maximum_common_delta(baseline_pairs, pair_vector(rx, kept))
        leave_anchor_max = max(leave_anchor_max, delta)
        leave_anchor_rows.append({
            "omitted_anchor": ANCHORS[omitted], "success": int(bool(rr.success)),
            "cost": float(rr.cost), "max_common_pair_distance_delta_mm": delta,
        })
    write_csv(args.out_dir / "V4IO_LEAVE_ONE_ANCHOR.csv", leave_anchor_rows)

    # Repeat the exact selected initialization twice and require exact numerical
    # replay to a tight floating-point tolerance.
    selected_init = production_init if selected_label == "production_init" else production_init * [1, 1, -1]
    replay1 = mod.solve_v4(fused, ids, selected_init)
    replay2 = mod.solve_v4(fused, ids, selected_init)
    deterministic_delta = max(
        float(np.max(np.abs(replay1[0] - replay2[0]))),
        float(np.max(np.abs(replay1[1] - replay2[1]))),
    )
    full_rms = residual_rms(x, delay, fused, ids)
    gates = {
        "complete_56_directions": all(row["pass"] for row in completeness),
        "full_solve_converged": bool(result.success),
        "full_pair_rms": full_rms <= THRESHOLDS["maximum_full_pair_rms_mm"],
        "split_geometry": split_pair_delta <= THRESHOLDS["maximum_split_pair_distance_delta_mm"],
        "split_delay": split_delay_delta <= THRESHOLDS["maximum_split_delay_delta_mm"],
        "multistart_geometry": multistart_max <= THRESHOLDS["maximum_multistart_pair_distance_delta_mm"],
        "deterministic_replay": deterministic_delta <= THRESHOLDS["deterministic_absolute_tolerance"],
    }
    qualification = {
        "schema": "biospur-current-room-v4io-qualification-v1",
        "verdict": "V4IO_LAYOUT_PASS" if all(gates.values()) else "V4IO_LAYOUT_FAIL",
        "relative_geometry_only": True,
        "mirror_selection": {
            "rule": "lowest frozen-objective cost among converged production and reflected initialization",
            "selected": selected_label,
            "candidate_costs": {item[0]: float(item[3].cost) for item in candidates},
            "selected_layer_gap_median_mm": result.physical_diagnostics.get("layer_gap_median_mm"),
        },
        "thresholds": THRESHOLDS,
        "gates": gates,
        "advisory_diagnostics": {
            "leave_one_pair_max_distance_delta_mm": leave_pair_max,
            "leave_one_anchor_max_common_distance_delta_mm": leave_anchor_max,
            "acceptance_role": (
                "reported, not gated: frozen V4-io defines no calibrated leave-one threshold; "
                "the acceptance stability gates are independent split/held-out and multistart replay"
            ),
        },
        "metrics": {
            "full_pair_rms_mm": full_rms,
            "split_pair_distance_max_delta_mm": split_pair_delta,
            "split_delay_max_delta_mm": split_delay_delta,
            "first_fit_on_last_half_rms_mm": first_on_last,
            "last_fit_on_first_half_rms_mm": last_on_first,
            "multistart_max_pair_distance_delta_mm": multistart_max,
            "leave_one_pair_max_distance_delta_mm": leave_pair_max,
            "leave_one_anchor_max_common_distance_delta_mm": leave_anchor_max,
            "deterministic_max_absolute_delta": deterministic_delta,
            "jacobian_condition_number": float(np.linalg.cond(result.jac)),
            "covariance": "NOT_EXPOSED_BY_FROZEN_V4IO_SOLVER",
        },
        "pairs_sha256": sha256_file(args.pairs),
        "production_solver": str(PRODUCTION),
    }
    (args.out_dir / "V4IO_QUALIFICATION.json").write_text(
        json.dumps(qualification, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    layout = {
        "version": "v4-io", "label": "V4-io", "anchor_ids": ids,
        "anchors": [
            {"id": anchor, "label": ANCHORS[anchor], "x_mm": float(x[anchor, 0]),
             "y_mm": float(x[anchor, 1]), "z_mm": float(x[anchor, 2]),
             "d_anchor_mm": float(delay[anchor])}
            for anchor in ids
        ],
        "tag_delay_mm": 0.0,
        "stats": {"inter_anchor_pair_rms_mm": full_rms, "n_pairs": len(fused),
                  "anchors_present": ANCHORS, "solver": "frozen production V4-io"},
        "extra": {"success": bool(result.success), **result.physical_diagnostics,
                  "relative_geometry_only": True, "mirror_selection": selected_label},
    }
    (args.out_dir / "V4IO_LAYOUT.json").write_text(
        json.dumps(layout, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    layout_rows = [dict(anchor) for anchor in layout["anchors"]]
    write_csv(args.out_dir / "V4IO_LAYOUT.csv", layout_rows)
    print(json.dumps(qualification, indent=2, sort_keys=True))
    return 0 if qualification["verdict"] == "V4IO_LAYOUT_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
