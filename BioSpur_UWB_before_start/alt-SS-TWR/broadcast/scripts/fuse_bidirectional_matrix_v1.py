#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


ANCHORS = tuple("ABCDEFGH")


@dataclass
class DirStats:
    n: int
    mean: float | None
    pstdev: float | None
    var_mean: float | None


def mean(vals: list[float]) -> float | None:
    if not vals:
        return None
    return float(sum(vals) / len(vals))


def pstdev(vals: list[float]) -> float | None:
    n = len(vals)
    if n == 0:
        return None
    if n == 1:
        return 0.0
    m = mean(vals)
    assert m is not None
    return math.sqrt(sum((x - m) ** 2 for x in vals) / n)


def dir_stats(vals: list[float], var_floor_mm2: float) -> DirStats:
    n = len(vals)
    m = mean(vals)
    s = pstdev(vals)
    if n == 0 or m is None or s is None:
        return DirStats(n=n, mean=None, pstdev=None, var_mean=None)
    if n <= 1:
        v = var_floor_mm2
    else:
        v = max((s * s) / n, var_floor_mm2)
    return DirStats(n=n, mean=m, pstdev=s, var_mean=v)


def collect_directional_samples(pairs_csv: Path) -> dict[tuple[str, str], list[float]]:
    directional: dict[tuple[str, str], list[float]] = {}
    with pairs_csv.open(encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            a = (row.get("a") or "").strip().upper()
            b = (row.get("b") or "").strip().upper()
            m = (row.get("master") or "").strip().upper()
            d_raw = row.get("dist_mm") or row.get("filt_mm") or row.get("filt")
            if a not in ANCHORS or b not in ANCHORS or m not in (a, b):
                continue
            if d_raw is None or str(d_raw).strip() == "":
                continue
            d = float(d_raw)
            src, dst = (a, b) if m == a else (b, a)
            directional.setdefault((src, dst), []).append(d)
    return directional


def z_bias(m1: float, v1: float, m2: float, v2: float) -> float:
    se = math.sqrt(v1 + v2)
    if se <= 0:
        return math.inf
    return (m1 - m2) / se


def fuse_pair(
    a: str,
    b: str,
    directional: dict[tuple[str, str], list[float]],
    z_thresh: float,
    min_dir_samples: int,
    var_floor_mm2: float,
) -> dict[str, Any]:
    vals_ab = directional.get((a, b), [])
    vals_ba = directional.get((b, a), [])
    s_ab = dir_stats(vals_ab, var_floor_mm2=var_floor_mm2)
    s_ba = dir_stats(vals_ba, var_floor_mm2=var_floor_mm2)

    out: dict[str, Any] = {
        "a": a,
        "b": b,
        "n_ab": s_ab.n,
        "n_ba": s_ba.n,
        "mean_ab": s_ab.mean,
        "mean_ba": s_ba.mean,
        "pstdev_ab": s_ab.pstdev,
        "pstdev_ba": s_ba.pstdev,
        "var_mean_ab": s_ab.var_mean,
        "var_mean_ba": s_ba.var_mean,
        "z": None,
        "decision": None,
        "distance_mm": None,
        "ci95_mm": None,
    }

    ab_ok = s_ab.n >= min_dir_samples and s_ab.mean is not None and s_ab.var_mean is not None
    ba_ok = s_ba.n >= min_dir_samples and s_ba.mean is not None and s_ba.var_mean is not None

    if ab_ok and ba_ok:
        z = z_bias(s_ab.mean, s_ab.var_mean, s_ba.mean, s_ba.var_mean)
        out["z"] = z
        if abs(z) > z_thresh:
            # Significant directional bias: choose more reliable direction (smaller mean variance).
            if s_ab.var_mean <= s_ba.var_mean:
                chosen_m, chosen_v, chosen_dir = s_ab.mean, s_ab.var_mean, "USE_A_TO_B"
            else:
                chosen_m, chosen_v, chosen_dir = s_ba.mean, s_ba.var_mean, "USE_B_TO_A"
            out["decision"] = f"BIAS:{chosen_dir}"
            out["distance_mm"] = chosen_m
            out["ci95_mm"] = 1.96 * math.sqrt(chosen_v)
            return out

        # No significant bias: inverse-variance weighted fusion.
        w1 = 1.0 / s_ab.var_mean
        w2 = 1.0 / s_ba.var_mean
        x_hat = (w1 * s_ab.mean + w2 * s_ba.mean) / (w1 + w2)
        v_hat = 1.0 / (w1 + w2)
        out["decision"] = "COMBINED_IVW"
        out["distance_mm"] = x_hat
        out["ci95_mm"] = 1.96 * math.sqrt(v_hat)
        return out

    # Fallback single direction if one side missing/insufficient.
    if ab_ok:
        out["decision"] = "SINGLE_A_TO_B"
        out["distance_mm"] = s_ab.mean
        out["ci95_mm"] = 1.96 * math.sqrt(s_ab.var_mean) if s_ab.var_mean is not None else None
        return out
    if ba_ok:
        out["decision"] = "SINGLE_B_TO_A"
        out["distance_mm"] = s_ba.mean
        out["ci95_mm"] = 1.96 * math.sqrt(s_ba.var_mean) if s_ba.var_mean is not None else None
        return out

    out["decision"] = "INSUFFICIENT"
    return out


def classical_mds(dist_mm: dict[tuple[str, str], float]) -> dict[str, list[float]]:
    n = len(ANCHORS)
    idx = {a: i for i, a in enumerate(ANCHORS)}
    D = np.zeros((n, n), dtype=float)
    for i, a in enumerate(ANCHORS):
        for j, b in enumerate(ANCHORS):
            if i == j:
                continue
            key = (a, b) if (a, b) in dist_mm else (b, a)
            D[i, j] = float(dist_mm[key])
    D2 = D ** 2
    J = np.eye(n) - np.ones((n, n)) / n
    B = -0.5 * J @ D2 @ J
    evals, evecs = np.linalg.eigh(B)
    order = np.argsort(evals)[::-1]
    evals = evals[order]
    evecs = evecs[:, order]
    k = 3
    evals_k = np.maximum(evals[:k], 0.0)
    L = np.diag(np.sqrt(evals_k))
    X = evecs[:, :k] @ L

    # Normalize frame: A at origin, B on +X.
    a_i = idx["A"]
    X = X - X[a_i]
    b_i = idx["B"]
    b = X[b_i]
    bx = np.linalg.norm(b[:2]) if np.linalg.norm(b[:2]) > 1e-9 else np.linalg.norm(b)
    if bx > 1e-9:
        theta = math.atan2(b[1], b[0])
        Rz = np.array(
            [
                [math.cos(-theta), -math.sin(-theta), 0],
                [math.sin(-theta), math.cos(-theta), 0],
                [0, 0, 1],
            ]
        )
        X = X @ Rz.T

    return {a: [float(X[idx[a], 0]), float(X[idx[a], 1]), float(X[idx[a], 2])] for a in ANCHORS}


def main() -> int:
    p = argparse.ArgumentParser(description="V1 bidirectional fusion for autopos matrix pairs.")
    p.add_argument("--pairs-csv", required=True, help="Input pairs_all.csv with columns a,b,dist_mm,master")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--z-thresh", type=float, default=2.0)
    p.add_argument("--min-dir-samples", type=int, default=3)
    p.add_argument("--var-floor-mm2", type=float, default=0.25)
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    directional = collect_directional_samples(Path(args.pairs_csv))

    rows: list[dict[str, Any]] = []
    final_dist: dict[tuple[str, str], float] = {}
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            r = fuse_pair(
                a,
                b,
                directional=directional,
                z_thresh=args.z_thresh,
                min_dir_samples=args.min_dir_samples,
                var_floor_mm2=args.var_floor_mm2,
            )
            rows.append(r)
            if r["distance_mm"] is not None:
                final_dist[(a, b)] = float(r["distance_mm"])

    # Write CSV decision table.
    csv_path = out_dir / "final_pair_distances.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "a",
                "b",
                "distance_mm",
                "ci95_mm",
                "decision",
                "z",
                "n_ab",
                "n_ba",
                "mean_ab",
                "mean_ba",
                "pstdev_ab",
                "pstdev_ba",
                "var_mean_ab",
                "var_mean_ba",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # Report JSON
    report = {
        "input_pairs_csv": str(Path(args.pairs_csv).resolve()),
        "z_thresh": args.z_thresh,
        "min_dir_samples": args.min_dir_samples,
        "var_floor_mm2": args.var_floor_mm2,
        "pair_rows": rows,
        "pair_count_with_distance": len(final_dist),
    }
    (out_dir / "pair_decision_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if len(final_dist) != 28:
        print(f"[warn] expected 28 pairs, got {len(final_dist)}")
        return 2

    coords = classical_mds(final_dist)
    coord_out = {
        "anchors": coords,
        "units": "mm",
        "note": "Classical MDS from fused pair distances (relative frame).",
    }
    (out_dir / "anchor_coords_v1.json").write_text(json.dumps(coord_out, indent=2) + "\n", encoding="utf-8")

    print(f"[ok] wrote {csv_path}")
    print(f"[ok] wrote {out_dir / 'pair_decision_report.json'}")
    print(f"[ok] wrote {out_dir / 'anchor_coords_v1.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
