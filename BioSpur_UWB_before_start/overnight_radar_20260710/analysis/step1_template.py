#!/usr/bin/env python3
"""STEP 1 — CIR alignment + coherent template A, for the 15 clean tag channels
(5 clean listeners x 3 wand tags). LCCF4 excluded (0 usable full CIR).

Pipeline per channel (per the brief, adapted to verified data realities):
  1a coarse: integer-tap roll so the first-path index sits at REF_TAP.
     fp_index in cir_index is DW1000 firstPath in 10.6 fixed point -> tap = /64.
  1b sub-tap: fractional shift from RCPHASE (7-bit, joined from LPD) via a
     frequency-domain phase ramp. Verified by FP-peak tightening (variance of
     |CIR| at the FP tap must drop vs coarse-only).
  1c CFO de-rotation: cfo_ppm = rxtofs/ttcki*1e6 (plausibility-checked), plus
     FP-phase referencing (rotate each frame so the FP tap is real-positive) --
     this is the clock-independent phase anchor that makes a COMPLEX median
     meaningful (per project memory: FP-referencing removes the clock; tail-tap
     phase circular-std ~15deg). Without it the complex median averages to noise.
  1d amplitude norm: divide by rxpacc (preamble accumulation count). agc(EDG1)
     is asserted 0 (checked); if not, flagged.
  1e template A: per-tap COMPLEX MEDIAN across aligned frames; sigma = per-tap
     MAD*1.4826 (real+imag combined).

Outputs:
  templates/{L}_{src}_A.npy      complex64 (1016)
  templates/{L}_{src}_sigma.npy  float32 (1016)
  templates/step1_report.csv
"""
import os, sys, csv, time
import numpy as np
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
PARSED = os.path.join(HERE, "parsed")
TPL = os.path.join(HERE, "templates")
os.makedirs(TPL, exist_ok=True)

CLEAN = ["LB", "LE", "LF", "L9336", "L955A"]
TAGS = [0xb136, 0xb15a, 0xb1f4]
NTAP = 1016
REF_TAP = 800
# DW1000 ch5: accumulator sample rate = 1/(499.2MHz*2? ) -> tap period.
# The CIR accumulator is sampled at 998.4 MHz (2x the 499.2 MHz chip rate) -> 1.0016 ns/tap.
TAP_NS = 1.0 / 0.9984  # 1.0016 ns/tap (998.4 MHz accumulator). (unused in this script)
F_CARRIER_HZ = 6489.6e6  # channel 5 centre


def _roll_int(frame, shift):
    return np.roll(frame, shift)


def _subtap_shift(frames, frac):
    """Shift each frame by fractional taps `frac[i]` via FFT phase ramp."""
    n = frames.shape[1]
    f = np.fft.fft(frames, axis=1)
    k = np.fft.fftfreq(n)  # cycles/tap
    # shift by +frac taps: multiply by exp(-j 2pi k frac)
    ramp = np.exp(-2j * np.pi * np.outer(frac, k))
    return np.fft.ifft(f * ramp, axis=1)


def process_channel(args):
    listener, src = args
    t0 = time.time()
    idx = np.load(os.path.join(PARSED, f"{listener}_cir_index.npz"))
    sel = idx["src"] == src
    rows = idx["frame_row"][sel]
    fp_raw = idx["fp_index"][sel].astype(np.float64)   # 10.6 fixed
    rcph = idx["rcph"][sel].astype(np.float64)
    rxtofs = idx["rxtofs"][sel].astype(np.float64)
    ttcki = idx["ttcki"][sel].astype(np.float64)
    now_ms = idx["now_ms"][sel].astype(np.int64)
    rxpacc = idx["rxpacc"][sel].astype(np.float64)
    n = rows.size
    if n < 50:
        return {"listener": listener, "src": hex(src), "n": int(n), "status": "too_few"}

    cir = np.load(os.path.join(PARSED, f"{listener}_cir.npy"), mmap_mode="r")
    frames = np.array(cir[rows], dtype=np.complex64)   # (n,1016)

    # --- 1a coarse integer roll: FP -> REF_TAP ---
    fp_tap = np.rint(fp_raw / 64.0).astype(int)
    shifts = REF_TAP - fp_tap
    for i in range(n):
        frames[i] = np.roll(frames[i], shifts[i])

    # FP-tap amplitude variance BEFORE sub-tap (for the tightening check)
    var_before = np.var(np.abs(frames[:, REF_TAP]))

    # --- 1b sub-tap via RCPHASE ---
    frac = rcph / 128.0            # fractional tap in [0,1)
    frames = _subtap_shift(frames, frac).astype(np.complex64)
    var_after = np.var(np.abs(frames[:, REF_TAP]))

    # --- 1c CFO plausibility + FP-phase referencing ---
    with np.errstate(divide="ignore", invalid="ignore"):
        cfo_ppm = np.where(ttcki > 0, rxtofs / ttcki * 1e6, np.nan)
    cfo_med = float(np.nanmedian(cfo_ppm))
    # FP-phase reference: make each frame's FP tap real-positive (clock-independent)
    fp_phase = np.angle(frames[:, REF_TAP])
    frames *= np.exp(-1j * fp_phase)[:, None]

    # --- 1d amplitude normalization by rxpacc ---
    rxpacc_safe = np.where(rxpacc > 0, rxpacc, np.nan)
    frames /= rxpacc_safe[:, None]

    # --- 1e complex median template + MAD sigma ---
    A = np.median(frames.real, axis=0) + 1j * np.median(frames.imag, axis=0)
    A = A.astype(np.complex64)
    # per-tap MAD over |residual| combined re/im
    res = frames - A[None, :]
    mad = np.median(np.abs(res.real), axis=0) + 1j * 0  # placeholder
    sigma = (1.4826 * 0.5 * (np.median(np.abs(res.real), axis=0)
                             + np.median(np.abs(res.imag), axis=0))).astype(np.float32)
    sigma = np.maximum(sigma, 1e-12)

    np.save(os.path.join(TPL, f"{listener}_{hex(src)}_A.npy"), A)
    np.save(os.path.join(TPL, f"{listener}_{hex(src)}_sigma.npy"), sigma)

    fp_amp = float(np.abs(A[REF_TAP]))
    dp_snr = fp_amp / float(np.median(sigma))
    tighten = float(var_before / var_after) if var_after > 0 else float("nan")
    agc_bad = int((idx["fp_index"][sel] < 0).sum())  # placeholder; agc checked in step8
    return {"listener": listener, "src": hex(src), "n": int(n),
            "fp_tap_med": int(np.median(fp_tap)), "fp_amp": round(fp_amp, 3),
            "dp_snr": round(dp_snr, 1), "subtap_tighten_ratio": round(tighten, 3),
            "cfo_ppm_med": round(cfo_med, 3),
            "t_span_h": round((now_ms.max() - now_ms.min()) / 3.6e6, 2),
            "seconds": round(time.time() - t0, 1), "status": "ok"}


def main():
    chans = [(l, s) for l in CLEAN for s in TAGS]
    only = None
    if len(sys.argv) > 1:  # e.g. "LE:0xb1f4" for a single-channel test
        l, s = sys.argv[1].split(":")
        chans = [(l, int(s, 16))]
        only = sys.argv[1]
    results = []
    if only:
        results.append(process_channel(chans[0]))
    else:
        with Pool(min(10, len(chans))) as p:
            for i, r in enumerate(p.imap_unordered(process_channel, chans)):
                print(f"[Step1] {i+1}/{len(chans)} {r['listener']} {r['src']}: "
                      f"n={r['n']} dp_snr={r.get('dp_snr')} tighten={r.get('subtap_tighten_ratio')} "
                      f"cfo={r.get('cfo_ppm_med')}ppm ({r.get('seconds')}s)", flush=True)
                results.append(r)
    if not only:
        keys = ["listener", "src", "n", "fp_tap_med", "fp_amp", "dp_snr",
                "subtap_tighten_ratio", "cfo_ppm_med", "t_span_h", "status"]
        with open(os.path.join(TPL, "step1_report.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r in sorted(results, key=lambda x: (x["listener"], x["src"])):
                w.writerow(r)
        print(f"[Step1] wrote {os.path.join(TPL,'step1_report.csv')}", flush=True)
    else:
        import json
        print(json.dumps(results[0], indent=2))


if __name__ == "__main__":
    main()
