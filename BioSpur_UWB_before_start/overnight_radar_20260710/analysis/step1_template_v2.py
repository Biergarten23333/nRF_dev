#!/usr/bin/env python3
"""STEP 1 v2 — coherent template A with CORRECT sub-tap alignment (BUG 1 fix).

BUG 1: step1 (v1) used RCPHASE (RX_TTCKO bits 31:25) as a fractional CIR tap
delay and shifted each frame by rcph/128 taps. RCPHASE is the *carrier phase*
of the received signal vs the sampling clock — NOT a sub-tap arrival time. The
sub-tap arrival time lives in FP_INDEX (LDE first-path index), a 10.6 fixed-
point value already logged per frame:
    fp_integer = fp_index_raw >> 6          (integer tap, used for coarse roll)
    fp_frac    = (fp_index_raw & 0x3F)/64   (sub-tap offset, in taps)
Ground-truth: main.c stores diag.firstPath raw; parsed fp_index is the full
16-bit value (median ~47854 -> /64 = 747.7 taps), fractional bits non-zero in
99.6% of frames — so NO reparse is needed, the parser already preserves it.

FIX (this file):
  - coarse: integer roll  shift = REF_TAP - (fp_index_raw >> 6)   (floor)
  - sub-tap: frequency-domain phase ramp by -fp_frac  (centres the true first
    path, which sits at REF_TAP + fp_frac after the floor roll, exactly on
    REF_TAP). Replaces the rcph/128 ramp entirely.
  - KEEP first-path complex-phase referencing (clock-independent) — correct.
  - KEEP rxpacc amplitude normalization — correct.

Also emits the alignment-quality comparison the review asked for: for every
channel, the FP-tap |amplitude| coefficient-of-variation (std/mean, lower =
tighter) under three sub-tap treatments sharing the same floor coarse roll:
    raw = coarse only          v1 = coarse + rcph/128       v2 = coarse - fp_frac

Outputs:
  templates_v2/{L}_{src}_A.npy      complex64 (1016)
  templates_v2/{L}_{src}_sigma.npy  float32 (1016)
  templates_v2/step1_v2_report.csv
  templates_v2/alignment_compare.csv   (raw/v1/v2 FP CV per channel)
"""
import os, sys, csv, time
import numpy as np
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
PARSED = os.path.join(HERE, "parsed")
TPL = os.path.join(HERE, "templates_v2")
os.makedirs(TPL, exist_ok=True)

CLEAN = ["LB", "LE", "LF", "L9336", "L955A"]
TAGS = [0xb136, 0xb15a, 0xb1f4]
NTAP = 1016
REF_TAP = 800


def _subtap_shift(frames, frac):
    """Shift each frame by fractional taps frac[i] via an FFT phase ramp.
    Convention: ramp = exp(-2j*pi*k*frac) delays (moves the peak to a HIGHER
    index) by +frac taps. So to ADVANCE (centre a peak sitting at +fp_frac),
    pass frac = -fp_frac."""
    n = frames.shape[1]
    f = np.fft.fft(frames, axis=1)
    k = np.fft.fftfreq(n)
    ramp = np.exp(-2j * np.pi * np.outer(frac, k))
    return np.fft.ifft(f * ramp, axis=1)


def _fp_cv(frames, rxpacc):
    """Coefficient of variation of |CIR[REF_TAP]| after rxpacc normalization.
    Isolates how consistently the FP peak is sampled at REF_TAP (alignment)."""
    amp = np.abs(frames[:, REF_TAP]).astype(np.float64)
    rp = np.where(rxpacc > 0, rxpacc, np.nan)
    a = amp / rp
    a = a[np.isfinite(a)]
    if a.size == 0 or np.mean(a) == 0:
        return np.nan, np.nan
    return float(np.std(a)), float(np.std(a) / np.mean(a))


def process_channel(args):
    listener, src = args
    t0 = time.time()
    idx = np.load(os.path.join(PARSED, f"{listener}_cir_index.npz"))
    sel = idx["src"] == src
    rows = idx["frame_row"][sel]
    fp_int = idx["fp_index"][sel].astype(np.int64)        # full 16-bit 10.6 fixed
    rcph = idx["rcph"][sel].astype(np.float64)
    now_ms = idx["now_ms"][sel].astype(np.int64)
    rxpacc = idx["rxpacc"][sel].astype(np.float64)
    n = rows.size
    if n < 50:
        return {"listener": listener, "src": hex(src), "n": int(n), "status": "too_few"}

    cir = np.load(os.path.join(PARSED, f"{listener}_cir.npy"), mmap_mode="r")
    frames = np.array(cir[rows], dtype=np.complex64)      # (n,1016)

    # --- 1a coarse integer roll (FLOOR): FP integer tap -> REF_TAP ---
    fp_integer = (fp_int >> 6).astype(int)
    fp_frac = ((fp_int & 0x3F).astype(np.float64)) / 64.0  # [0,1)
    shifts = REF_TAP - fp_integer
    for i in range(n):
        frames[i] = np.roll(frames[i], shifts[i])

    # --- alignment comparison (raw / v1-rcph / v2-fp_frac), same coarse roll ---
    cv_raw = _fp_cv(frames, rxpacc)
    v1 = _subtap_shift(frames, rcph / 128.0).astype(np.complex64)      # OLD (wrong)
    cv_v1 = _fp_cv(v1, rxpacc)
    v2 = _subtap_shift(frames, -fp_frac).astype(np.complex64)          # NEW (correct)
    cv_v2 = _fp_cv(v2, rxpacc)
    del v1

    # --- 1b sub-tap alignment = v2 (FP_INDEX fractional) ---
    frames = v2
    del v2

    # --- 1c first-path complex-phase referencing (clock-independent) ---
    fp_phase = np.angle(frames[:, REF_TAP])
    frames *= np.exp(-1j * fp_phase)[:, None]

    # --- 1d amplitude normalization by rxpacc ---
    rxpacc_safe = np.where(rxpacc > 0, rxpacc, np.nan)
    frames /= rxpacc_safe[:, None]

    # --- 1e complex median template + MAD sigma ---
    A = (np.median(frames.real, axis=0) + 1j * np.median(frames.imag, axis=0)).astype(np.complex64)
    res = frames - A[None, :]
    sigma = (1.4826 * 0.5 * (np.median(np.abs(res.real), axis=0)
                             + np.median(np.abs(res.imag), axis=0))).astype(np.float32)
    sigma = np.maximum(sigma, 1e-12)

    np.save(os.path.join(TPL, f"{listener}_{hex(src)}_A.npy"), A)
    np.save(os.path.join(TPL, f"{listener}_{hex(src)}_sigma.npy"), sigma)

    fp_amp = float(np.abs(A[REF_TAP]))
    dp_snr = fp_amp / float(np.median(sigma))
    return {"listener": listener, "src": hex(src), "n": int(n),
            "fp_tap_med": int(np.median(fp_integer)), "fp_frac_med": round(float(np.median(fp_frac)), 3),
            "fp_amp": round(fp_amp, 3), "dp_snr": round(dp_snr, 1),
            "cv_raw": round(cv_raw[1], 4), "cv_v1_rcph": round(cv_v1[1], 4),
            "cv_v2_fpfrac": round(cv_v2[1], 4),
            "std_raw": round(cv_raw[0], 4), "std_v1_rcph": round(cv_v1[0], 4),
            "std_v2_fpfrac": round(cv_v2[0], 4),
            "v2_tighter_than_v1": bool(cv_v2[1] < cv_v1[1]),
            "seconds": round(time.time() - t0, 1), "status": "ok"}


def main():
    chans = [(l, s) for l in CLEAN for s in TAGS]
    only = sys.argv[1] if len(sys.argv) > 1 else None
    if only:
        l, s = only.split(":")
        import json
        print(json.dumps(process_channel((l, int(s, 16))), indent=2))
        return
    results = []
    with Pool(min(10, len(chans))) as p:
        for i, r in enumerate(p.imap_unordered(process_channel, chans)):
            results.append(r)
            if r.get("status") == "ok":
                print(f"[Step1v2] {i+1}/{len(chans)} {r['listener']}<-{r['src']}: "
                      f"n={r['n']} dp_snr={r['dp_snr']}  FP-CV raw={r['cv_raw']} "
                      f"v1={r['cv_v1_rcph']} v2={r['cv_v2_fpfrac']}  "
                      f"{'V2 TIGHTER' if r['v2_tighter_than_v1'] else 'v2 not better'}", flush=True)
    ok = [r for r in results if r.get("status") == "ok"]
    keys = ["listener", "src", "n", "fp_tap_med", "fp_frac_med", "fp_amp", "dp_snr",
            "cv_raw", "cv_v1_rcph", "cv_v2_fpfrac", "std_raw", "std_v1_rcph",
            "std_v2_fpfrac", "v2_tighter_than_v1"]
    with open(os.path.join(TPL, "step1_v2_report.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in sorted(ok, key=lambda x: (x["listener"], x["src"])):
            w.writerow(r)
    # alignment-compare CSV + top-3 cleanest channel highlight
    with open(os.path.join(TPL, "alignment_compare.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["listener", "src", "dp_snr", "cv_raw",
                           "cv_v1_rcph", "cv_v2_fpfrac", "v2_tighter_than_v1"],
                           extrasaction="ignore")
        w.writeheader()
        for r in sorted(ok, key=lambda x: -x["dp_snr"]):
            w.writerow(r)
    top3 = sorted(ok, key=lambda x: -x["dp_snr"])[:3]
    print("\n[Step1v2] === 3 CLEANEST channels: FP-tap amplitude CV (lower=tighter) ===")
    print(f"  {'channel':<16}{'dp_snr':>8}{'raw':>10}{'v1(rcph)':>11}{'v2(fp_frac)':>13}   verdict")
    for r in top3:
        verdict = "v2 tighter ✓" if r["v2_tighter_than_v1"] else "v2 NOT better"
        print(f"  {r['listener']+'<-'+r['src']:<16}{r['dp_snr']:>8}{r['cv_raw']:>10.4f}"
              f"{r['cv_v1_rcph']:>11.4f}{r['cv_v2_fpfrac']:>13.4f}   {verdict}")
    nt = sum(r["v2_tighter_than_v1"] for r in ok)
    print(f"\n[Step1v2] v2 tighter than v1 on {nt}/{len(ok)} channels")
    print(f"[Step1v2] wrote {TPL}/ (15 templates + step1_v2_report.csv + alignment_compare.csv)")


if __name__ == "__main__":
    main()
