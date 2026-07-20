#!/usr/bin/env python3
"""Morning analysis of the overnight interleaved TX-power sweep.
Reads per-cell cell_meta.json (+ tr_labeled.csv). Produces REPORT.md, results.json, figures.
Deliverables: A bias vs power, B valid%/miss vs power, C temp coefficient (mm/degC),
D power<->temp decorrelation, E lock events. Robust: missing matplotlib -> skip figures.
"""
import json, glob, os, statistics, sys
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
PRESETS = ["MAX", "M3", "M6", "M12", "POR"]
DB_ABOVE_FLOOR = {"MAX": 8.5, "M3": 5.5, "M6": 2.5, "M12": 0.0, "POR": 4.0}
TEMP_C_PER_LSB = 1.14   # DW1000 SAR temp scale; absolute uncalibrated, slope correct
TEMP_REF_LSB = 82       # nominal 23C cal point (absolute temp approximate only)

def raw_to_C(raw):
    return 23.0 + TEMP_C_PER_LSB * (raw - TEMP_REF_LSB)

def load_cells():
    cells = []
    for f in sorted(glob.glob(os.path.join(ROOT, "round_*", "**", "cell_meta.json"), recursive=True)):
        try:
            cells.append(json.load(open(f)))
        except Exception:
            pass
    return cells

def main():
    cells_all = load_cells()
    if not cells_all:
        print("no cells found"); return
    # exclude dead cells (0 TR rows / no tags) — e.g. the round-1 MAX collision cell —
    # so they don't drag the pooled per-power stats.
    cells = [c for c in cells_all if c.get("n_tr_rows", 0) > 0 and c.get("tags_present")]
    n_excluded = len(cells_all) - len(cells)
    out = {"n_cells": len(cells), "n_excluded_dead": n_excluded,
           "rounds": max(c.get("round", 0) for c in cells)}

    # ---- B: valid%/ge7/ge8 vs power (pooled) ----
    B = {}
    for p in PRESETS:
        cs = [c for c in cells if c["preset"] == p]
        if not cs: continue
        B[p] = {"n_cells": len(cs),
                "ratio_ge7": round(statistics.mean(c["ratio_ge7"] for c in cs), 4),
                "ratio_ge8": round(statistics.mean(c["ratio_ge8"] for c in cs), 4),
                "valid_pct": round(statistics.mean(c["valid_pct_overall"] for c in cs), 1)}
    out["B_valid_vs_power"] = B

    # ---- A: bias vs power (per tag x anchor, pooled cell means) ----
    links = set()
    for c in cells:
        links |= set(c.get("per_link", {}).keys())
    A = {}
    for lk in sorted(links):
        row = {}
        for p in PRESETS:
            vals = [c["per_link"][lk]["mean_mm"] for c in cells
                    if c["preset"] == p and lk in c.get("per_link", {})]
            if vals: row[p] = round(statistics.mean(vals), 1)
        if len(row) >= 2:
            row["swing_mm"] = round(max(row.values()) - min(row.values()), 1)
            A[lk] = row
    out["A_bias_vs_power"] = A
    swings = [v["swing_mm"] for v in A.values() if "swing_mm" in v]
    out["A_median_swing_mm"] = round(statistics.median(swings), 1) if swings else None
    out["A_max_swing_mm"] = round(max(swings), 1) if swings else None

    # ---- C: temp coefficient (per link: range vs temp, pooled across power) ----
    C = {}
    coeffs = []
    for lk in sorted(links):
        xs, ys = [], []
        for c in cells:
            if lk in c.get("per_link", {}) and "temp_raw_mean" in c:
                xs.append(raw_to_C(c["temp_raw_mean"]))
                ys.append(c["per_link"][lk]["mean_mm"])
        if len(xs) >= 6 and (max(xs) - min(xs)) >= 1.0:
            A_ = np.polyfit(xs, ys, 1)
            C[lk] = {"mm_per_C": round(float(A_[0]), 2), "n": len(xs),
                     "temp_span_C": round(max(xs) - min(xs), 1)}
            coeffs.append(float(A_[0]))
    out["C_temp_coeff_per_link"] = C
    out["C_median_mm_per_C"] = round(statistics.median(coeffs), 2) if coeffs else None

    # ---- D: decorrelation (power vs temp) ----
    D = {}
    for p in PRESETS:
        ts = [raw_to_C(c["temp_raw_mean"]) for c in cells if c["preset"] == p and "temp_raw_mean" in c]
        if ts:
            D[p] = {"n": len(ts), "temp_mean_C": round(statistics.mean(ts), 1),
                    "temp_min_C": round(min(ts), 1), "temp_max_C": round(max(ts), 1)}
    out["D_power_vs_temp"] = D
    all_t = [raw_to_C(c["temp_raw_mean"]) for c in cells if "temp_raw_mean" in c]
    out["D_temp_span_C"] = round(max(all_t) - min(all_t), 1) if all_t else None

    # ---- E: lock events (sustained >150mm offset vs link median, per cell) ----
    link_med = {lk: statistics.median([c["per_link"][lk]["mean_mm"] for c in cells
                if lk in c.get("per_link", {})]) for lk in links}
    E = []
    for c in cells:
        for lk, d in c.get("per_link", {}).items():
            if abs(d["mean_mm"] - link_med[lk]) > 150 or d["std_mm"] > 200:
                E.append({"round": c["round"], "preset": c["preset"], "link": lk,
                          "mean_mm": d["mean_mm"], "median_mm": round(link_med[lk], 0),
                          "offset_mm": round(d["mean_mm"] - link_med[lk], 0), "std_mm": d["std_mm"]})
    out["E_lock_events"] = E[:200]
    out["E_lock_event_count"] = len(E)

    with open(os.path.join(ROOT, "results.json"), "w") as f:
        json.dump(out, f, indent=1)

    # ---- figures (best-effort) ----
    figs = []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        # B: valid/ge7/ge8 vs power
        xs = [DB_ABOVE_FLOOR[p] for p in PRESETS if p in B]
        plt.figure()
        for k in ("ratio_ge7", "ratio_ge8"):
            plt.plot(xs, [B[p][k]*100 for p in PRESETS if p in B], "o-", label=k)
        plt.plot(xs, [B[p]["valid_pct"] for p in PRESETS if p in B], "s-", label="valid%")
        plt.xlabel("dB above floor"); plt.ylabel("%"); plt.legend(); plt.title("Link success vs TX power")
        plt.savefig(os.path.join(ROOT, "fig_valid_vs_power.png"), dpi=110); plt.close(); figs.append("fig_valid_vs_power.png")
        # D: power vs temp scatter
        plt.figure()
        for p in PRESETS:
            ts = [raw_to_C(c["temp_raw_mean"]) for c in cells if c["preset"] == p and "temp_raw_mean" in c]
            plt.scatter([DB_ABOVE_FLOOR[p]]*len(ts), ts, alpha=0.5, label=p)
        plt.xlabel("dB above floor"); plt.ylabel("chip temp (C, approx)"); plt.title("Decorrelation: power vs temp")
        plt.savefig(os.path.join(ROOT, "fig_decorrelation.png"), dpi=110); plt.close(); figs.append("fig_decorrelation.png")
        # C: temp vs time
        plt.figure()
        cc = sorted([c for c in cells if "temp_raw_mean" in c], key=lambda c: c["epoch"])
        t0 = cc[0]["epoch"] if cc else 0
        plt.plot([(c["epoch"]-t0)/3600 for c in cc], [raw_to_C(c["temp_raw_mean"]) for c in cc], ".-")
        plt.xlabel("hours"); plt.ylabel("chip temp (C, approx)"); plt.title("Temperature across the night")
        plt.savefig(os.path.join(ROOT, "fig_temp_vs_time.png"), dpi=110); plt.close(); figs.append("fig_temp_vs_time.png")
    except Exception as e:
        print("figures skipped:", e)

    # ---- REPORT.md ----
    with open(os.path.join(ROOT, "REPORT.md"), "w") as f:
        f.write("# Overnight Power Sweep — Report\n\n")
        f.write(f"Cells: {out['n_cells']}  Rounds: {out['rounds']}  Temp span: {out['D_temp_span_C']} C (approx)\n\n")
        f.write("## B. Link success vs TX power (main result)\n\n| preset | dB | cells | ge7 | ge8 | valid% |\n|---|---|---|---|---|---|\n")
        for p in PRESETS:
            if p in B:
                f.write(f"| {p} | {DB_ABOVE_FLOOR[p]} | {B[p]['n_cells']} | {B[p]['ratio_ge7']} | {B[p]['ratio_ge8']} | {B[p]['valid_pct']} |\n")
        f.write(f"\n## A. Bias vs power\nMedian per-link swing across 5 powers: **{out['A_median_swing_mm']} mm** (max {out['A_max_swing_mm']} mm).\n")
        f.write("Verdict: bias power-insensitive if median swing is within per-link noise (~25-40mm).\n\n")
        f.write(f"## C. Temperature coefficient\nMedian **{out['C_median_mm_per_C']} mm/degC** across {len(C)} links (pooled, power decorrelated).\n\n")
        f.write("## D. Decorrelation check\n\n| preset | cells | temp_mean | temp_min | temp_max |\n|---|---|---|---|---|\n")
        for p in PRESETS:
            if p in D:
                f.write(f"| {p} | {D[p]['n']} | {D[p]['temp_mean_C']} | {D[p]['temp_min_C']} | {D[p]['temp_max_C']} |\n")
        f.write(f"\n## E. Lock events\n{out['E_lock_event_count']} cell-links with >150mm offset or >200mm std.\n\n")
        f.write("Figures: " + ", ".join(figs) + "\n")
    print("analysis done:", out["n_cells"], "cells ->", os.path.join(ROOT, "REPORT.md"))

if __name__ == "__main__":
    main()
