#!/usr/bin/env python3
"""STEP 2 — coherent stability / template A shelf-life, per 15 tag channel.
Divide the 10h into 30-min windows; per window build the FP-referenced complex
median template A_window and measure its drift vs the whole-night A_total in
units of the noise floor sigma_A. drift_rms>1 sigma => the stored template no
longer matches the live channel. Also track CFO(t) as an environmental proxy.
Alignment matches Step 1 (coarse FP->800, RCPHASE sub-tap, FP-phase reference,
rxpacc amplitude norm)."""
import os, csv
import numpy as np
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
PARSED = os.path.join(HERE, "parsed"); TPL = os.path.join(HERE, "templates")
OUT = os.path.join(HERE, "stability"); os.makedirs(OUT, exist_ok=True)
CLEAN = ["LB", "LE", "LF", "L9336", "L955A"]; TAGS = [0xb136, 0xb15a, 0xb1f4]
NTAP = 1016; REF_TAP = 800; WIN_MS = 30 * 60 * 1000
F_CARRIER = 6489.6e6; C = 299.792458e3  # mm/s -> use c in mm/s? keep mm via /Hz below


def align(frames, rcph):
    n = frames.shape[1]
    f = np.fft.fft(frames, axis=1); k = np.fft.fftfreq(n)
    frames = np.fft.ifft(f * np.exp(-2j * np.pi * np.outer(rcph / 128.0, k)), axis=1)
    fp_ph = np.angle(frames[:, REF_TAP])
    frames = frames * np.exp(-1j * fp_ph)[:, None]
    return frames.astype(np.complex64)


def process(args):
    listener, src = args
    idx = np.load(os.path.join(PARSED, f"{listener}_cir_index.npz"))
    sel = idx["src"] == src
    rows = idx["frame_row"][sel]; fp_raw = idx["fp_index"][sel].astype(float)
    rcph = idx["rcph"][sel].astype(float); now = idx["now_ms"][sel].astype(np.int64)
    rxpacc = idx["rxpacc"][sel].astype(float); rxtofs = idx["rxtofs"][sel].astype(float)
    ttcki = idx["ttcki"][sel].astype(float)
    n = rows.size
    if n < 200:
        return {"listener": listener, "src": hex(src), "status": "too_few"}
    cir = np.load(os.path.join(PARSED, f"{listener}_cir.npy"), mmap_mode="r")
    frames = np.array(cir[rows], dtype=np.complex64)
    shifts = REF_TAP - np.rint(fp_raw / 64.0).astype(int)
    for i in range(n):
        frames[i] = np.roll(frames[i], shifts[i])
    frames = align(frames, rcph)
    rxpacc_safe = np.where(rxpacc > 0, rxpacc, np.nan)
    frames /= rxpacc_safe[:, None]
    A_total = (np.median(frames.real, axis=0) + 1j * np.median(frames.imag, axis=0)).astype(np.complex64)
    sigma = np.load(os.path.join(TPL, f"{listener}_{hex(src)}_sigma.npy"))
    sig_mean = float(np.mean(sigma))

    t0 = now.min()
    wbin = ((now - t0) // WIN_MS).astype(int)
    win_ids = np.unique(wbin)
    drift = []; wtime_h = []; cfo_win = []
    with np.errstate(divide="ignore", invalid="ignore"):
        cfo_ppm = np.where(ttcki > 0, rxtofs / ttcki * 1e6, np.nan)
    for w in win_ids:
        mask = wbin == w
        if mask.sum() < 30:
            continue
        Aw = np.median(frames[mask].real, axis=0) + 1j * np.median(frames[mask].imag, axis=0)
        dr = np.sqrt(np.mean(np.abs(Aw - A_total) ** 2)) / max(sig_mean, 1e-12)
        drift.append(float(dr)); wtime_h.append(float((w * WIN_MS) / 3.6e6))
        cfo_win.append(float(np.nanmedian(cfo_ppm[mask])))
    drift = np.array(drift); wtime_h = np.array(wtime_h); cfo_win = np.array(cfo_win)
    max_drift = float(drift.max()) if drift.size else np.nan
    # time to first exceed 1 sigma
    over = np.where(drift > 1.0)[0]
    t_1sig = float(wtime_h[over[0]]) if over.size else float("inf")
    # cfo linear drift ppm/hour
    if wtime_h.size > 2:
        cfo_slope = float(np.polyfit(wtime_h, cfo_win, 1)[0])
    else:
        cfo_slope = np.nan
    np.savez_compressed(os.path.join(OUT, f"{listener}_{hex(src)}_drift.npz"),
                        wtime_h=wtime_h, drift_sigma=drift, cfo_ppm=cfo_win)
    return {"listener": listener, "src": hex(src), "n": int(n), "windows": int(drift.size),
            "max_drift_sigma": round(max_drift, 3),
            "time_to_1sigma_h": ("inf" if t_1sig == float("inf") else round(t_1sig, 2)),
            "median_drift_sigma": round(float(np.median(drift)), 3),
            "cfo_drift_ppm_per_h": round(cfo_slope, 4), "status": "ok"}


def main():
    chans = [(l, s) for l in CLEAN for s in TAGS]
    results = []
    with Pool(min(10, len(chans))) as p:
        for r in p.imap_unordered(process, chans):
            results.append(r)
            if r.get("status") == "ok":
                print(f"[Step2] {r['listener']}<-{r['src']}: max_drift={r['max_drift_sigma']}sigma "
                      f"t_1sigma={r['time_to_1sigma_h']}h cfo_drift={r['cfo_drift_ppm_per_h']}ppm/h", flush=True)
    ok = [r for r in results if r.get("status") == "ok"]
    keys = ["listener", "src", "n", "windows", "max_drift_sigma", "time_to_1sigma_h",
            "median_drift_sigma", "cfo_drift_ppm_per_h"]
    with open(os.path.join(OUT, "drift_summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore"); w.writeheader()
        for r in sorted(ok, key=lambda x: (x["listener"], x["src"])):
            w.writerow(r)
    never = [r for r in ok if r["time_to_1sigma_h"] == "inf"]
    print(f"\n[Step2] {len(ok)} channels. Templates that NEVER drift >1sigma in 10h: "
          f"{len(never)}/{len(ok)}")
    mx = [r['max_drift_sigma'] for r in ok]
    print(f"[Step2] max-drift across channels: median={np.median(mx):.2f} worst={max(mx):.2f} sigma")
    print(f"[Step2] wrote {OUT}/drift_summary.csv")


if __name__ == "__main__":
    main()
