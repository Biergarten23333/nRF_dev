#!/usr/bin/env python3
"""Solver-correctness check for the 4-unknown d_tag path (pg_lib.solve_pos).

Proves the negative result in REPORT.md is a property of the DATA, not a broken
solver: inject a known common-mode range offset into synthetic frames and confirm
the 4-unknown solve recovers it (<1 mm bias) while keeping position clean, whereas
the 3-unknown solve smears the same offset into ~15x the position error.

Pure synthetic, no capture data. Deterministic (seeded).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start")
sys.path.insert(0, str(REPO / "logs/geiger_scan_20260711_161258_8anchor/analysis"))
from pg_lib import solve_pos  # noqa: E402

# V4-io-like anchor geometry + per-anchor delays
P = np.array([[0, 0, 0], [4712, 0, 0], [4427, 3031, 0], [265, 2853, -231],
              [601, -230, -1568], [4740, 94, -1484], [2300, 3000, -1400],
              [2400, -200, -1500]], float)
DLY = {i: v for i, v in enumerate([0, 12.8, 60, 30.9, 18.6, 13, 31.6, 60])}
X0 = P.mean(axis=0)
NOISE_MM = 25.0
N = 400


def trial(inject_mm, estimate_d_tag, seed):
    rng = np.random.default_rng(seed)
    dtags, pos_err = [], []
    for _ in range(N):
        true = np.array([2700, 900, -800.0]) + rng.normal(0, 250, 3)
        rg = {i: float(np.linalg.norm(P[i] - true) + DLY[i] + inject_mm + rng.normal(0, NOISE_MM))
              for i in range(8)}
        if estimate_d_tag:
            pos, _ids, _rms, dt = solve_pos(P, rg, X0, delays=DLY, estimate_d_tag=True)
            dtags.append(dt)
        else:
            pos, _ids, _rms = solve_pos(P, rg, X0, delays=DLY)
        pos_err.append(float(np.linalg.norm(pos - true)))
    return (np.median(dtags) if dtags else np.nan,
            np.std(dtags) if dtags else np.nan,
            np.median(pos_err))


def main():
    print("4-unknown recovery (d_tag co-estimated):")
    for inj in (0.0, 150.0, -80.0):
        med, std, pe = trial(inj, True, seed=100 + int(inj))
        print(f"  inject {inj:+7.1f} mm -> d_tag_med {med:+7.1f} (std {std:4.1f}), pos_err {pe:5.1f} mm")
    print("3-unknown (offset smeared into position):")
    for inj in (0.0, 150.0):
        _m, _s, pe = trial(inj, False, seed=200 + int(inj))
        print(f"  inject {inj:+7.1f} mm ->                        pos_err {pe:5.1f} mm")


if __name__ == "__main__":
    main()
