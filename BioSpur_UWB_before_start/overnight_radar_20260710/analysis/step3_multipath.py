#!/usr/bin/env python3
"""STEP 3 — multipath extraction from the 15 coherent templates.
Direct path sits at REF_TAP=800 (Step 1 aligned it there). Zero FP+/-3, then
every tap cluster above 5*sigma is a room reflection. Report excess delay,
bistatic excess range, amplitude vs direct path, and complex amplitude."""
import os, csv, json, glob
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, "templates")
OUT = os.path.join(HERE, "multipath")
os.makedirs(OUT, exist_ok=True)
REF_TAP = 800
FP_GUARD = 3
TAP_NS = 1.0 / 0.9984          # 1.0016 ns/tap: CIR accumulator = 998.4 MHz (2x 499.2 MHz chip rate); c*tap = 0.30m = range res c/(2*BW)
C = 299.792458                  # mm/ns
SIGMA_K = 5.0

rows = []
per_chan = {}
for apath in sorted(glob.glob(os.path.join(TPL, "*_A.npy"))):
    base = os.path.basename(apath)[:-6]           # strip _A.npy
    listener, src = base.split("_")
    A = np.load(apath)
    sigma = np.load(os.path.join(TPL, f"{base}_sigma.npy"))
    mag = np.abs(A)
    # DW1000 LDE marks the leading edge at REF_TAP=800, but the direct-path
    # RRC pulse main lobe peaks a few taps later at several x that amplitude and
    # extends ~10 taps. The whole main lobe must be excluded (not just FP+/-3),
    # and reflector amplitude referenced to the direct-path PEAK, not the leading
    # edge. Find the peak in [FP, FP+15], then walk out to where the envelope
    # first drops below DP_END_FRAC of the peak for 2 consecutive taps.
    dp_peak = REF_TAP + int(np.argmax(mag[REF_TAP:REF_TAP + 16]))
    fp_amp = mag[dp_peak]                       # direct-path strength (peak)
    DP_END_FRAC = 0.25
    ml_end = dp_peak
    t = dp_peak + 1
    while t < REF_TAP + 40:
        if mag[t] < DP_END_FRAC * fp_amp and mag[t + 1] < DP_END_FRAC * fp_amp:
            ml_end = t
            break
        t += 1
    else:
        ml_end = REF_TAP + 12
    mainlobe_end = ml_end
    thresh = SIGMA_K * sigma
    over = mag > thresh
    over[REF_TAP - FP_GUARD: mainlobe_end + 1] = False   # exclude full direct-path main lobe
    # only post-FP taps are physical reflections (arrive later); flag pre-FP separately
    refls = []
    i = 0
    n = over.size
    while i < n:
        if over[i]:
            j = i
            while j < n and over[j]:
                j += 1
            cl = np.arange(i, j)
            pk = cl[int(np.argmax(mag[cl]))]
            excess_tap = pk - REF_TAP
            refl = {
                "tap_index": int(pk),
                "excess_delay_ns": round(float(excess_tap * TAP_NS), 3),
                "bistatic_excess_mm": round(float(excess_tap * TAP_NS * C), 1),
                "amplitude_dB": round(float(20 * np.log10(mag[pk] / fp_amp)), 2),
                "snr_over_sigma": round(float(mag[pk] / sigma[pk]), 1),
                "re": float(A[pk].real), "im": float(A[pk].imag),
                "pre_fp": bool(excess_tap < 0),
            }
            refls.append(refl)
            i = j
        else:
            i += 1
    post = [r for r in refls if not r["pre_fp"]]
    per_chan[base] = refls
    blind_mm = round(float((mainlobe_end - REF_TAP) * TAP_NS * C), 1)   # near-in blind zone
    with open(os.path.join(OUT, f"{base}_reflectors.json"), "w") as f:
        json.dump({"listener": listener, "src": src, "fp_tap": REF_TAP,
                   "dp_peak_tap": int(dp_peak), "mainlobe_end_tap": int(mainlobe_end),
                   "near_in_blind_mm": blind_mm, "n_reflectors": len(post),
                   "reflectors": refls}, f, indent=2)
    # delay spread (post-FP, amplitude-weighted std of excess delay)
    if post:
        d = np.array([r["excess_delay_ns"] for r in post])
        w = np.array([10 ** (r["amplitude_dB"] / 20) for r in post])
        ds = float(np.sqrt(np.average((d - np.average(d, weights=w)) ** 2, weights=w)))
        strongest = max(post, key=lambda r: r["amplitude_dB"])
    else:
        ds = 0.0
        strongest = {"amplitude_dB": None, "bistatic_excess_mm": None}
    rows.append({"listener": listener, "src": src, "dp_peak_tap": int(dp_peak),
                 "mainlobe_end_tap": int(mainlobe_end), "near_in_blind_mm": blind_mm,
                 "n_reflectors_postFP": len(post),
                 "n_preFP_artifacts": len(refls) - len(post),
                 "delay_spread_ns": round(ds, 2),
                 "strongest_dB": strongest["amplitude_dB"],
                 "strongest_excess_mm": strongest["bistatic_excess_mm"]})
    print(f"[Step3] {base}: dp_peak@{dp_peak} ML_end@{mainlobe_end} blind<{blind_mm}mm | "
          f"{len(post)} reflectors, strongest {strongest['amplitude_dB']}dB @ "
          f"{strongest['bistatic_excess_mm']}mm, spread {ds:.1f}ns", flush=True)

with open(os.path.join(OUT, "all_reflectors_summary.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(sorted(rows, key=lambda r: (r["listener"], r["src"])))

nref = [r["n_reflectors_postFP"] for r in rows]
print(f"\n[Step3] {len(rows)} channels: reflectors/channel "
      f"mean={np.mean(nref):.1f} median={np.median(nref):.0f} max={max(nref)}")
print(f"[Step3] wrote {OUT}/all_reflectors_summary.csv")
