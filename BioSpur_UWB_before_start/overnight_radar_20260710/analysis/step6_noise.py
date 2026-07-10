#!/usr/bin/env python3
"""STEP 6 — direct-path ranging-noise / channel-quality table.

Adapted to the passive-listener reality: the listener logs NO range field, so
the spec's "scalar range_mm" noise is unavailable. Instead we use the DW1000
LDE first-path index (fp_index, 10.6 fixed) as the raw per-frame arrival-time
estimate: its frame-to-frame std on a static link = the raw ranging jitter that
a TWR range from this link would inherit. Computed for ALL 11 sources x 5 clean
listeners (anchors via LRD, tags via LPD). The finer COHERENT precision (mm via
CIR phase) is characterized separately by Step 1's direct-path SNR and the
Step 2 phase-drift analysis; raw LDE jitter is the honest scalar-domain number.
"""
import os, csv
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PARSED = os.path.join(HERE, "parsed")
OUT = os.path.join(HERE, "noise"); os.makedirs(OUT, exist_ok=True)
CLEAN = ["LB", "LE", "LF", "L9336", "L955A"]
SRCS = [0xa100, 0xa101, 0xa102, 0xa103, 0xa104, 0xa105, 0xa106, 0xa107,
        0xb136, 0xb15a, 0xb1f4]
TAP_NS = 1.0 / 0.9984   # 1.0016 ns/tap: CIR accumulator = 998.4 MHz (2x 499.2 MHz chip rate)
C = 299.792458   # mm/ns

# optional: pull dp_snr for the 15 tag channels from Step 1
snr = {}
try:
    import csv as _csv
    with open(os.path.join(HERE, "templates", "step1_report.csv")) as f:
        for r in _csv.DictReader(f):
            snr[(r["listener"], r["src"])] = float(r["dp_snr"])
except Exception:
    pass

rows = []
for L in CLEAN:
    z = np.load(os.path.join(PARSED, f"{L}_scalar.npz"))
    d = z["data"]; cols = list(z["cols"]); ci = {c: i for i, c in enumerate(cols)}
    src = d[:, ci["src"]]; fp = d[:, ci["fp_index"]].astype(np.float64) / 64.0
    stdn = d[:, ci["stdnoise"]].astype(np.float64); rxp = d[:, ci["rxpacc"]].astype(np.float64)
    kind = d[:, ci["kind"]]
    for s in SRCS:
        m = src == s
        n = int(m.sum())
        if n < 100:
            continue
        fp_s = fp[m]
        # robust std (MAD) to resist LDE outlier taps
        med = np.median(fp_s)
        mad = np.median(np.abs(fp_s - med)) * 1.4826
        jit_mm = float(mad * TAP_NS * C)
        is_tag = s >= 0xb000
        rows.append({
            "listener": L, "src": hex(s), "role": "tag" if is_tag else "anchor",
            "n": n, "fp_tap_med": round(float(med), 2),
            "fp_tap_mad": round(float(mad), 3),
            "range_jitter_mm": round(jit_mm, 1),
            "stdnoise_mean": round(float(np.mean(stdn[m])), 1),
            "rxpacc_mean": round(float(np.mean(rxp[m])), 0),
            "dp_snr_cir": snr.get((L, hex(s)), ""),  # only tag channels have CIR
        })

rows.sort(key=lambda r: r["range_jitter_mm"])
keys = ["listener", "src", "role", "n", "fp_tap_med", "fp_tap_mad",
        "range_jitter_mm", "stdnoise_mean", "rxpacc_mean", "dp_snr_cir"]
with open(os.path.join(OUT, "per_channel_noise.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)

jit = np.array([r["range_jitter_mm"] for r in rows])
med_jit = float(np.median(jit))
print(f"[Step6] {len(rows)} channels. raw-LDE range jitter (MAD): "
      f"median={med_jit:.1f}mm  min={jit.min():.1f}  max={jit.max():.1f}")
print("  cleanest 5:")
for r in rows[:5]:
    print(f"    {r['listener']:<6}<-{r['src']} ({r['role']:<6}) jitter={r['range_jitter_mm']}mm "
          f"n={r['n']:>7,} snr_cir={r['dp_snr_cir']}")
print("  dirtiest 5 (>2x median flagged):")
for r in rows[-5:]:
    flag = " <-- >2x median" if r["range_jitter_mm"] > 2 * med_jit else ""
    print(f"    {r['listener']:<6}<-{r['src']} ({r['role']:<6}) jitter={r['range_jitter_mm']}mm{flag}")
print(f"[Step6] wrote {OUT}/per_channel_noise.csv")
