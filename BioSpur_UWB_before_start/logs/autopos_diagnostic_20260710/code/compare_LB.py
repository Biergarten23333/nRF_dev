#!/usr/bin/env python3
"""Isolate the effect of Listener L-B on Anchor B's ranging.

Three conditions, all 2026-07-10, reference = 5 untouched anchors A,C,D,E,G:
  premove  : L-B ~10cm from B, powered ON, stand against wall
  postmove : L-B ~40-50cm from B, powered ON, stand off wall
  LBoff    : L-B powered OFF and moved far, stand off wall   <-- new test
Clean isolation = postmove vs LBoff (both off-wall; only L-B changed).
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diagnostic as dg

OUT = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/logs/autopos_diagnostic_20260710"
CORE = list("ACDEG")
COND = [
    ("premove  (L-B ~10cm ON, wall)",   f"{OUT}/raw/sweep/summary.json"),
    ("postmove (L-B ~40cm ON, off-wall)", f"{OUT}/raw/sweep_postmove/summary.json"),
    ("LBoff    (L-B OFF+far, off-wall)",  f"{OUT}/raw/sweep_LBoff/summary.json"),
]


def row(name, D):
    pos, rms5 = dg.clean6_frame(D, clean=CORE)
    out = {"core": round(rms5, 1)}
    for a in ("B", "E", "F"):
        ml = dg.multilat_vs_clean6(pos, D, a, clean=CORE)
        w = max(ml["resid"].items(), key=lambda kv: abs(kv[1]))
        out[a] = (ml["rms"], f"{a}-{w[0]}", round(w[1]))
    # B-A specific link median + MAD (the historically broken link)
    ba = dg.undirected_vals(D, "B", "A")
    out["BA_med"] = round(float(np.median(ba)), 0)
    out["BA_mad"] = round(dg.mad(ba), 0)
    return out


def main():
    print(f"{'condition':38}{'core':>6}{'B_RMS':>7}{'B_worst':>16}{'B-A med':>9}{'B-A MAD':>9}"
          f"{'E_RMS':>7}{'F_RMS':>7}")
    for name, path in COND:
        if not os.path.exists(path):
            print(f"{name:38}  (missing)"); continue
        D = dg.load_directed(path)
        r = row(name, D)
        bworst = f"{r['B'][1]} {r['B'][2]}"
        print(f"{name:38}{r['core']:>6}{r['B'][0]:>7}{bworst:>16}{r['BA_med']:>9}{r['BA_mad']:>9}"
              f"{r['E'][0]:>7}{r['F'][0]:>7}")
    print("\nClean isolation = postmove vs LBoff (both off-wall; only L-B power/position changed).")
    print("If B_RMS & B-A are ~unchanged -> L-B was NOT the cause (mechanical/cable). "
          "If they drop a lot -> L-B mattered.")


if __name__ == "__main__":
    main()
