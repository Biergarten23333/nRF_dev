#!/usr/bin/env python3
import argparse
import csv
import itertools
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares


REPO_ROOT = Path(__file__).resolve().parents[1]
ANCHOR_LABELS = "ABCDEFGH"
LOWER_IDS = {0, 1, 2, 3}
UPPER_IDS = {4, 5, 6, 7}


def load_layout(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    anchors = {}
    for item in payload["anchors"]:
        anchors[int(item["id"])] = np.array(
            [float(item["x_mm"]), float(item["y_mm"]), float(item["z_mm"])], dtype=float
        )
    return anchors


def load_range_series(path: Path):
    series = defaultdict(list)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            anchor_id = int(row["anchor_id"])
            series[anchor_id].append(float(row["filt_mm"]))
    return series


def subset_label(subset):
    return ",".join(ANCHOR_LABELS[i] for i in subset)


def subset_plane_kind(subset):
    lower = sum(1 for idx in subset if idx in LOWER_IDS)
    upper = sum(1 for idx in subset if idx in UPPER_IDS)
    if lower == 4:
        return "lower_coplane"
    if upper == 4:
        return "upper_coplane"
    if lower == 2 and upper == 2:
        return "balanced_2_2"
    if lower == 3 and upper == 1:
        return "weak_3_1"
    if lower == 1 and upper == 3:
        return "weak_1_3"
    return f"mixed_{lower}_{upper}"


def solve_point(anchor_positions, distances_mm, initial_guess):
    def residuals(x):
        point = np.asarray(x, dtype=float)
        pred = np.linalg.norm(anchor_positions - point[None, :], axis=1)
        return pred - distances_mm

    result = least_squares(
        residuals,
        x0=np.asarray(initial_guess, dtype=float),
        method="lm",
        max_nfev=200,
    )
    point = np.asarray(result.x, dtype=float)
    res = residuals(point)
    rms = float(np.sqrt(np.mean(res * res)))
    max_abs = float(np.max(np.abs(res)))
    return point, rms, max_abs


def evaluate_subset(subset, anchors, series, z_weight, residual_weight):
    usable = min(len(series[idx]) for idx in subset)
    if usable <= 0:
        return None

    anchor_positions = np.stack([anchors[idx] for idx in subset], axis=0)
    centroid = np.mean(anchor_positions, axis=0)
    guess = np.array([centroid[0], centroid[1], np.mean(anchor_positions[:, 2]) * 0.45], dtype=float)

    positions = []
    rms_values = []
    max_values = []

    for sample_idx in range(usable):
        distances = np.array([series[idx][sample_idx] for idx in subset], dtype=float)
        point, rms, max_abs = solve_point(anchor_positions, distances, guess)
        guess = point
        positions.append(point)
        rms_values.append(rms)
        max_values.append(max_abs)

    points = np.stack(positions, axis=0)
    mean = np.mean(points, axis=0)
    std = np.std(points, axis=0)
    geom = math.sqrt(std[0] ** 2 + std[1] ** 2 + (z_weight * std[2]) ** 2)
    score = geom + residual_weight * float(np.mean(rms_values))

    return {
        "subset": subset_label(subset),
        "anchor_ids": list(subset),
        "plane_kind": subset_plane_kind(subset),
        "sample_count": int(usable),
        "position_mean_mm": {
            "x": float(mean[0]),
            "y": float(mean[1]),
            "z": float(mean[2]),
        },
        "position_std_mm": {
            "x": float(std[0]),
            "y": float(std[1]),
            "z": float(std[2]),
        },
        "residual_mean_mm": {
            "rms": float(np.mean(rms_values)),
            "max": float(np.mean(max_values)),
        },
        "residual_std_mm": {
            "rms": float(np.std(rms_values)),
            "max": float(np.std(max_values)),
        },
        "score": float(score),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate all 4-anchor subsets for Ref115 using an 8-anchor calibration ranges.csv session."
    )
    parser.add_argument(
        "--ranges-csv",
        default="logs/tag_sessions/ref115_autopos_20260321_livecheck/ranges.csv",
    )
    parser.add_argument(
        "--layout-json",
        default="data/anchor_layout_ah_runtime.json",
    )
    parser.add_argument(
        "--result-json",
        default="logs/tag_sessions/ref115_four_anchor_subset_eval_20260321.json",
    )
    parser.add_argument("--z-weight", type=float, default=2.0)
    parser.add_argument("--residual-weight", type=float, default=0.05)
    args = parser.parse_args()

    ranges_csv = (REPO_ROOT / args.ranges_csv).resolve()
    layout_json = (REPO_ROOT / args.layout_json).resolve()
    result_json = (REPO_ROOT / args.result_json).resolve()
    result_json.parent.mkdir(parents=True, exist_ok=True)

    anchors = load_layout(layout_json)
    series = load_range_series(ranges_csv)

    results = []
    for subset in itertools.combinations(range(8), 4):
        result = evaluate_subset(subset, anchors, series, args.z_weight, args.residual_weight)
        if result is not None:
            results.append(result)

    results.sort(key=lambda item: item["score"])

    payload = {
        "ranges_csv": str(ranges_csv),
        "layout_json": str(layout_json),
        "z_weight": args.z_weight,
        "residual_weight": args.residual_weight,
        "best_subset": results[0] if results else None,
        "results": results,
    }
    result_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if results:
        best = results[0]
        print(f"result_json: {result_json}")
        print(f"best_subset: {best['subset']}")
        print(f"best_plane_kind: {best['plane_kind']}")
        print(f"best_score: {best['score']:.3f}")
        print(
            "best_std_mm: "
            f"x={best['position_std_mm']['x']:.2f} "
            f"y={best['position_std_mm']['y']:.2f} "
            f"z={best['position_std_mm']['z']:.2f}"
        )
    else:
        print("No valid subsets evaluated.")


if __name__ == "__main__":
    raise SystemExit(main())
