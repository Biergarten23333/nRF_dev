#!/usr/bin/env python3
from __future__ import annotations

"""
V3 fusion: MVUE-ish bidirectional fusion with robust variance (MAD).

Input: pairs_all.csv (from autopos_extract_pairs_from_sweep_summary.py)
  Columns: a,b,master,dist_mm,quality_percent,raw_mm,ok,fail

We treat rows where master==a as direction a->b (AB), and master==b as BA.
We compute:
  - robust mean per direction via median (and also keep mean for bias)
  - robust variance per direction via MAD^2 (scaled, Gaussian-consistent)
  - fused distance D_ab (MVUE linear comb weighted by inverse variance)
  - fused variance (sigma^2_fused)
  - high_bias flag: abs(mean_ab - mean_ba) > bias_sigma_mult * sqrt(var_ab + var_ba)

This is intentionally self-contained and does not depend on pandas.
"""

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


ANCHORS = tuple("ABCDEFGH")


def mad_sigma(samples: np.ndarray) -> float:
    if samples.size == 0:
        return float("inf")
    med = float(np.median(samples))
    mad = float(np.median(np.abs(samples - med)))
    # 1.4826 makes MAD consistent with Gaussian sigma.
    return 1.4826 * mad


def robust_var_mad(samples: np.ndarray, min_sigma: float) -> float:
    sig = mad_sigma(samples)
    if not math.isfinite(sig) or sig <= 0.0:
        sig = float(np.std(samples)) if samples.size else float("inf")
    sig = max(sig, float(min_sigma))
    return sig * sig


def fuse_mvue(
    mean_ab: float,
    var_ab: float,
    mean_ba: float,
    var_ba: float,
) -> tuple[float, float]:
    # If one direction is missing or has infinite variance, fall back to the other.
    if not math.isfinite(var_ab) or var_ab <= 0.0:
        return mean_ba, var_ba
    if not math.isfinite(var_ba) or var_ba <= 0.0:
        return mean_ab, var_ab
    denom = var_ab + var_ba
    if denom <= 0.0:
        return 0.5 * (mean_ab + mean_ba), float("inf")
    # MVUE: weights proportional to inverse variances.
    fused = (var_ba * mean_ab + var_ab * mean_ba) / denom
    fused_var = (var_ab * var_ba) / denom
    return float(fused), float(fused_var)


def pair_key(a: str, b: str) -> str:
    a = a.upper().strip()
    b = b.upper().strip()
    if a == b:
        raise ValueError("self pair")
    if a > b:
        a, b = b, a
    return f"{a}-{b}"


def main() -> int:
    ap = argparse.ArgumentParser(description="V3 robust MVUE-ish bidirectional fusion.")
    ap.add_argument("--pairs-csv", required=True, help="pairs_all.csv")
    ap.add_argument("--out-dir", required=True, help="output dir")
    ap.add_argument("--min-dir-samples", type=int, default=20, help="min samples per direction to be considered reliable")
    ap.add_argument("--min-sigma-mm", type=float, default=3.0, help="MAD sigma floor per direction (mm)")
    ap.add_argument("--bias-sigma-mult", type=float, default=3.0, help="bias significance threshold in sigmas")
    args = ap.parse_args()

    pairs_csv = Path(args.pairs_csv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    by_dir: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"ab": [], "ba": []})
    counts_ok: dict[str, dict[str, int]] = defaultdict(lambda: {"ab": 0, "ba": 0})

    with pairs_csv.open(encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            a = (row.get("a") or "").strip().upper()
            b = (row.get("b") or "").strip().upper()
            master = (row.get("master") or "").strip().upper()
            if a not in ANCHORS or b not in ANCHORS or master not in ANCHORS:
                continue
            if a == b:
                continue
            try:
                ok = int(row.get("ok") or "0")
            except Exception:
                ok = 0
            if ok != 1:
                continue
            try:
                d_mm = float(row.get("dist_mm") or row.get("distance_mm") or "")
            except Exception:
                continue
            if not math.isfinite(d_mm) or d_mm <= 0:
                continue

            key = pair_key(a, b)
            if master == a:
                by_dir[key]["ab"].append(d_mm)
                counts_ok[key]["ab"] += 1
            elif master == b:
                by_dir[key]["ba"].append(d_mm)
                counts_ok[key]["ba"] += 1

    fused_rows: list[dict[str, Any]] = []
    dist_map_mm: dict[str, int] = {}
    pair_stats: dict[str, Any] = {}

    min_sigma_m = float(args.min_sigma_mm) / 1000.0
    for key in sorted(by_dir.keys()):
        a, b = key.split("-", 1)
        ab = np.asarray(by_dir[key]["ab"], dtype=float) / 1000.0
        ba = np.asarray(by_dir[key]["ba"], dtype=float) / 1000.0

        n_ab = int(ab.size)
        n_ba = int(ba.size)

        # Direction means: use mean for bias and MVUE, but variance is MAD-robust.
        mean_ab = float(np.mean(ab)) if n_ab else float("nan")
        mean_ba = float(np.mean(ba)) if n_ba else float("nan")
        var_ab = robust_var_mad(ab, min_sigma_m) if n_ab else float("inf")
        var_ba = robust_var_mad(ba, min_sigma_m) if n_ba else float("inf")

        # If we don't have enough samples, inflate variance to down-weight.
        if n_ab and n_ab < args.min_dir_samples:
            var_ab *= (args.min_dir_samples / max(1, n_ab))
        if n_ba and n_ba < args.min_dir_samples:
            var_ba *= (args.min_dir_samples / max(1, n_ba))

        fused_m, fused_var_m2 = fuse_mvue(mean_ab, var_ab, mean_ba, var_ba)

        # Bias check (directional asymmetry).
        high_bias = False
        bias_m = float("nan")
        if n_ab and n_ba and math.isfinite(var_ab) and math.isfinite(var_ba):
            bias_m = abs(mean_ab - mean_ba)
            thresh = float(args.bias_sigma_mult) * math.sqrt(max(0.0, var_ab + var_ba))
            high_bias = bias_m > thresh

        fused_mm = int(round(fused_m * 1000.0)) if math.isfinite(fused_m) and fused_m > 0 else 0
        var_mm2 = float(fused_var_m2 * 1e6) if math.isfinite(fused_var_m2) else float("inf")
        dist_map_mm[key] = fused_mm

        fused_rows.append(
            {
                "a": a,
                "b": b,
                "distance_mm": fused_mm,
                "sigma_mm": math.sqrt(var_mm2) if math.isfinite(var_mm2) and var_mm2 >= 0 else "",
                "var_mm2": var_mm2 if math.isfinite(var_mm2) else "",
                "n_ab": n_ab,
                "n_ba": n_ba,
                "mean_ab_mm": float(mean_ab * 1000.0) if math.isfinite(mean_ab) else "",
                "mean_ba_mm": float(mean_ba * 1000.0) if math.isfinite(mean_ba) else "",
                "bias_mm": float(bias_m * 1000.0) if math.isfinite(bias_m) else "",
                "high_bias": bool(high_bias),
            }
        )
        pair_stats[key] = {
            "n_ab": n_ab,
            "n_ba": n_ba,
            "var_ab_mm2": float(var_ab * 1e6) if math.isfinite(var_ab) else None,
            "var_ba_mm2": float(var_ba * 1e6) if math.isfinite(var_ba) else None,
            "bias_mm": float(bias_m * 1000.0) if math.isfinite(bias_m) else None,
            "high_bias": bool(high_bias),
        }

    out_csv = out_dir / "final_pair_distances_v3.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fused_rows[0].keys()) if fused_rows else [])
        if fused_rows:
            w.writeheader()
            for row in fused_rows:
                w.writerow(row)

    matrix_json = out_dir / "inter_anchor_matrix_v3fused.json"
    payload = {
        "units": "mm",
        "anchors": list(ANCHORS),
        "distances": dist_map_mm,
        "pair_stats": pair_stats,
        "source": {"pairs_csv": str(pairs_csv.resolve())},
        "notes": [
            "V3 fused distances via robust MAD variance + MVUE-ish bidirectional fusion.",
            "Use solve_anchor_layout_v3_full.py for antenna-delay + Tukey IRLS.",
        ],
    }
    matrix_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "pairs_csv": str(pairs_csv.resolve()),
        "out_dir": str(out_dir.resolve()),
        "min_dir_samples": int(args.min_dir_samples),
        "min_sigma_mm": float(args.min_sigma_mm),
        "bias_sigma_mult": float(args.bias_sigma_mult),
        "output_csv": str(out_csv.resolve()),
        "output_matrix_json": str(matrix_json.resolve()),
        "pair_count": len(dist_map_mm),
    }
    (out_dir / "v3_fusion_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    print(f"[ok] wrote {out_csv} rows={len(fused_rows)}")
    print(f"[ok] wrote {matrix_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

