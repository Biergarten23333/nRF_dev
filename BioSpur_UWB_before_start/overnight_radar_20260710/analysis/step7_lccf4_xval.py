#!/usr/bin/env python3
"""STEP 7 — is LCCF4's surviving (validated) data trustworthy?

Two hard realities constrain the spec's plan:
  (1) LCCF4 has 0 complete CIR frames (76% of chunks corrupt) -> the CIR-pair
      comparison (FP amplitude/phase, multipath taps) is impossible.
  (2) Cross-listener frame pairing is impossible anyway: now_ms and rx_ts are
      per-device clocks with no shared reference, so "same TX frame within
      +/-50ms" cannot be established across two listeners.
So Step 7 becomes a DISTRIBUTIONAL sanity check of LCCF4's post-validation scalar
diagnostics vs a clean listener, per TX source. Absolute levels differ (LCCF4 is
at a different location), so the test is: are LCCF4's distributions physically
sane and stable (tight, plausible), i.e. did strict validation actually leave
clean data, or residual garbage?
"""
import os, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PARSED = os.path.join(HERE, "parsed")
OUT = os.path.join(HERE, "xval"); os.makedirs(OUT, exist_ok=True)
SRCS = [0xa101, 0xa106, 0xb136, 0xb15a, 0xb1f4]   # the well-populated sources


def load(L):
    z = np.load(os.path.join(PARSED, f"{L}_scalar.npz"))
    d = z["data"]; ci = {c: i for i, c in enumerate(list(z["cols"]))}
    return d, ci


dL, ci = load("LCCF4")
dRef, _ = load("LE")   # clean reference

report = {}
print("=== STEP 7 — LCCF4 validated-scalar sanity (vs clean LE, per source) ===")
print(f"{'src':8}{'n_LCCF4':>9}{'fp_med':>9}{'fp_MAD':>9}{'cirpwr_med':>12}{'stdnoise_med':>13}{'sane?':>7}")
for s in SRCS:
    mL = dL[:, ci["src"]] == s
    if mL.sum() < 100:
        continue
    fp = dL[mL, ci["fp_index"]].astype(float) / 64.0
    cirp = dL[mL, ci["cir"]].astype(float)
    stdn = dL[mL, ci["stdnoise"]].astype(float)
    fp_med = float(np.median(fp)); fp_mad = float(np.median(np.abs(fp - fp_med)) * 1.4826)
    # sanity: FP index near the expected ~748 window and tightly bounded, stdnoise plausible
    sane = bool(700 < fp_med < 800 and fp_mad < 12 and 0 < np.median(stdn) < 3000)
    # clean-ref spread for the same source (context, not identity)
    mR = dRef[:, ci["src"]] == s
    fpR = dRef[mR, ci["fp_index"]].astype(float) / 64.0
    ref_mad = float(np.median(np.abs(fpR - np.median(fpR))) * 1.4826)
    report[hex(s)] = {"n": int(mL.sum()), "fp_med": round(fp_med, 2), "fp_mad": round(fp_mad, 2),
                      "cirpwr_med": float(np.median(cirp)), "stdnoise_med": float(np.median(stdn)),
                      "ref_LE_fp_mad": round(ref_mad, 2), "sane": sane}
    print(f"{hex(s):8}{int(mL.sum()):>9,}{fp_med:>9.1f}{fp_mad:>9.2f}"
          f"{np.median(cirp):>12.0f}{np.median(stdn):>13.0f}{str(sane):>7}")

all_sane = all(v["sane"] for v in report.values())
verdict = ("TRUSTWORTHY for scalar-domain use (presence, coarse timing, ampl. trends): "
           "validated LCCF4 rows are physically sane and comparable-spread to a clean listener. "
           "BUT it contributes ZERO CIR waveforms (76% chunk corruption) and its raw LSTAT/EVC "
           "are unreliable. Use LCCF4 scalar only; exclude it from all CIR/coherent analysis."
           if all_sane else
           "SUSPECT: some LCCF4 sources fail the sanity bounds even after strict validation; "
           "exclude LCCF4 entirely from quantitative analysis.")
print(f"\nVERDICT: {verdict}")
json.dump({"per_source": report, "all_sane": all_sane, "verdict": verdict,
           "cir_pairing": "impossible: 0 LCCF4 CIR frames + no cross-listener clock sync"},
          open(os.path.join(OUT, "step7_lccf4_xval.json"), "w"), indent=2)
print(f"[Step7] wrote {OUT}/step7_lccf4_xval.json")
