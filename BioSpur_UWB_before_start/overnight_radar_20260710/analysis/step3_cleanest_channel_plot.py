#!/usr/bin/env python3
"""Step 3 deep-dive on the cleanest channel LB<-0xb1f4 (dp_snr=40.7).
Plot |A| dB vs tap (full 1016), 5sigma threshold, marked reflectors; then the
tap-by-tap amplitude-relative-to-sigma for taps 50-200 beyond FP (tail sanity)."""
import os, json
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, "templates"); OUT = os.path.join(HERE, "step3")
os.makedirs(OUT, exist_ok=True)
L, SRC = "LB", "0xb1f4"; REF_TAP = 800; SIGMA_K = 5.0
TAP_NS = 1.0 / 0.9984; C = 299.792458  # 1.0016 ns/tap (998.4 MHz accumulator); unused in this plot

A = np.load(os.path.join(TPL, f"{L}_{SRC}_A.npy"))
sig = np.load(os.path.join(TPL, f"{L}_{SRC}_sigma.npy"))
mag = np.abs(A); taps = np.arange(len(mag))
refl = json.load(open(os.path.join(HERE, "multipath", f"{L}_{SRC}_reflectors.json")))
dp_peak = refl["dp_peak_tap"]; ml_end = refl["mainlobe_end_tap"]
fp_amp = mag[dp_peak]

magdB = 20 * np.log10(np.maximum(mag, 1e-9) / fp_amp)
thrdB = 20 * np.log10(np.maximum(SIGMA_K * sig, 1e-9) / fp_amp)
rtaps = [r["tap_index"] for r in refl["reflectors"]]

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9))
# --- panel 1: full CIR ---
ax1.plot(taps, magdB, lw=0.8, color="steelblue", label="|A| (dB re direct-path peak)")
ax1.plot(taps, thrdB, lw=1.0, color="red", ls="--", label=f"{SIGMA_K:.0f}σ detection threshold")
ax1.axvspan(REF_TAP, ml_end, color="orange", alpha=0.2, label=f"direct-path main lobe [{REF_TAP},{ml_end}]")
ax1.axvline(REF_TAP, color="k", lw=0.6, ls=":")
for i, t in enumerate(rtaps):
    ax1.plot(t, magdB[t], "v", color="lime", ms=11, mec="k",
             label="detected reflector (>5σ)" if i == 0 else None)
    ax1.annotate(f"{refl['reflectors'][i]['snr_over_sigma']:.1f}σ", (t, magdB[t]),
                 textcoords="offset points", xytext=(0, 8), fontsize=7, ha="center")
ax1.set_xlim(0, 1016); ax1.set_ylim(-60, 3)
ax1.set_xlabel("CIR tap index"); ax1.set_ylabel("amplitude (dB)")
ax1.set_title(f"Step 3 — cleanest channel {L}←{SRC} (dp_snr=40.7): full 1016-tap CIR template, "
              f"{refl['n_reflectors']} reflectors >5σ")
ax1.legend(loc="upper right", fontsize=8); ax1.grid(alpha=0.3)

# --- panel 2: tail sanity, taps 50-200 beyond FP, in SNR (mag/sigma) units ---
lo, hi = REF_TAP + 50, REF_TAP + 200
snr = mag / np.maximum(sig, 1e-9)
seg = np.arange(lo, hi)
ax2.plot(seg, snr[lo:hi], lw=0.9, color="darkgreen")
ax2.axhline(SIGMA_K, color="red", ls="--", label="5σ threshold")
ax2.axhline(1.0, color="gray", ls=":", label="1σ (noise floor)")
ax2.axhline(float(np.median(snr[lo:hi])), color="purple", ls="-.",
            label=f"segment median = {np.median(snr[lo:hi]):.2f}σ")
ax2.set_xlabel("CIR tap index (FP+50 … FP+200)"); ax2.set_ylabel("amplitude / σ  (SNR units)")
ax2.set_title("Tail region 50–200 taps beyond FP — amplitude relative to per-tap σ")
ax2.legend(fontsize=8, loc="upper right"); ax2.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "step3_cleanest_LB_0xb1f4.png"), dpi=150)
plt.close(fig)

# --- numbers ---
print(f"=== {L}<-{SRC}  (cleanest, dp_snr=40.7) ===")
print(f"FP tap={REF_TAP}  dp_peak={dp_peak}  main-lobe end={ml_end}")
print(f"Reflectors >5σ: {refl['n_reflectors']}  at taps {rtaps}")
print("\nTail region FP+50..FP+200 (taps 850–1000), amplitude in σ units:")
s = snr[lo:hi]
print(f"  median={np.median(s):.2f}σ  mean={np.mean(s):.2f}σ  max={np.max(s):.2f}σ "
      f"(@tap {lo+int(np.argmax(s))})  p95={np.percentile(s,95):.2f}σ")
print(f"  fraction of taps >3σ: {100*np.mean(s>3):.1f}%   >5σ: {100*np.mean(s>5):.1f}%")
exp_false = len(s) * 2 * (1 - 0.9999997)  # rough Rayleigh>5σ expectation is ~0; contextual
print(f"  taps in segment: {len(s)}; any >5σ would be a reflector (none expected from noise)")
print(f"\n[wrote] {OUT}/step3_cleanest_LB_0xb1f4.png")
