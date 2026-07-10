#!/usr/bin/env python3
"""STEP 2 v2 — template shelf-life with a PROPER HOLDOUT (BUG 2 fix).

BUG 2: step2 (v1) built A_total from ALL frames across the full 10.5 h, then
measured each 30-min window's drift against A_total. Because A_total contains
the window under test, drift is underestimated (no holdout).

FIX:
  - Freeze the template on ONLY the first 30 min:
        A_frozen   = complex median of frames in [t0, t0+30min]
        sigma_frozen = 1.4826 * MAD of those same frames (per tap)
  - Every later 30-min window (t=30min .. 10.5h) is PURE HOLDOUT:
        A_window   = complex median of that window
        drift_rms  = sqrt(mean(|A_window - A_frozen|^2)) / mean(sigma_frozen)
  - Per holdout window also track:
        FP-tap amplitude drift (%), FP-tap phase drift -> mm path,
        n false-alarm taps where |A_window - A_frozen| > 5*sigma_frozen
        (taps that would trigger a detection in an empty room).

Alignment is the CORRECTED step1_v2 pipeline (FP_INDEX sub-tap, NOT RCPHASE):
  floor coarse roll -> -fp_frac sub-tap -> first-path phase reference -> rxpacc norm.

Outputs (stability_v2/):
  {L}_{src}_holdout.npz   per-window arrays
  drift_summary.csv
  drift_curves_holdout.png
  false_alarm_rate.png
  stability_v2_summary.json
"""
import os, csv, json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
PARSED = os.path.join(HERE, "parsed")
OUT = os.path.join(HERE, "stability_v2"); os.makedirs(OUT, exist_ok=True)
CLEAN = ["LB", "LE", "LF", "L9336", "L955A"]; TAGS = [0xb136, 0xb15a, 0xb1f4]
NTAP = 1016; REF_TAP = 800; WIN_MS = 30 * 60 * 1000
FREEZE_MS = 30 * 60 * 1000           # first 30 min freezes the template
C = 299_792_458.0; F_CARRIER = 6489.6e6
LAMBDA_MM = C / F_CARRIER * 1e3       # 46.19 mm
SIGMA_K = 5.0
COLORS = plt.cm.tab20(np.linspace(0, 1, 15))


def align_norm(frames, fp_int, rxpacc):
    """CORRECTED alignment (step1_v2): floor coarse roll -> -fp_frac sub-tap ->
    first-path phase reference -> rxpacc amplitude normalization."""
    n = frames.shape[1]
    fp_integer = (fp_int >> 6).astype(int)
    fp_frac = ((fp_int & 0x3F).astype(np.float64)) / 64.0
    for i in range(frames.shape[0]):
        frames[i] = np.roll(frames[i], REF_TAP - fp_integer[i])
    f = np.fft.fft(frames, axis=1); k = np.fft.fftfreq(n)
    frames = np.fft.ifft(f * np.exp(-2j * np.pi * np.outer(-fp_frac, k)), axis=1)
    fp_ph = np.angle(frames[:, REF_TAP])
    frames = frames * np.exp(-1j * fp_ph)[:, None]
    rp = np.where(rxpacc > 0, rxpacc, np.nan)
    frames = frames / rp[:, None]
    return frames.astype(np.complex64)


def cmedian(fr):
    return (np.median(fr.real, axis=0) + 1j * np.median(fr.imag, axis=0)).astype(np.complex64)


def process(args):
    listener, src = args
    idx = np.load(os.path.join(PARSED, f"{listener}_cir_index.npz"))
    sel = idx["src"] == src
    rows = idx["frame_row"][sel]
    fp_int = idx["fp_index"][sel].astype(np.int64)
    now = idx["now_ms"][sel].astype(np.int64)
    rxpacc = idx["rxpacc"][sel].astype(np.float64)
    n = rows.size
    if n < 200:
        return {"listener": listener, "src": hex(src), "status": "too_few"}
    order = np.argsort(now)
    rows, fp_int, now, rxpacc = rows[order], fp_int[order], now[order], rxpacc[order]
    cir = np.load(os.path.join(PARSED, f"{listener}_cir.npy"), mmap_mode="r")
    frames = align_norm(np.array(cir[rows], dtype=np.complex64), fp_int, rxpacc)

    t0 = now.min()
    rel = now - t0
    frozen_mask = rel < FREEZE_MS
    if frozen_mask.sum() < 50:
        return {"listener": listener, "src": hex(src), "status": "too_few_frozen"}
    A_frozen = cmedian(frames[frozen_mask])
    resf = frames[frozen_mask] - A_frozen[None, :]
    sigma_frozen = (1.4826 * 0.5 * (np.median(np.abs(resf.real), axis=0)
                                    + np.median(np.abs(resf.imag), axis=0))).astype(np.float64)
    sigma_frozen = np.maximum(sigma_frozen, 1e-12)
    sig_mean = float(np.mean(sigma_frozen))
    fp_amp_frozen = np.abs(A_frozen[REF_TAP])
    fp_ph_frozen = np.angle(A_frozen[REF_TAP])

    wbin = (rel // WIN_MS).astype(int)
    hold = np.unique(wbin[wbin >= 1])       # holdout windows only (>=30min)
    wt_h, drift, fp_amp_d, fp_ph_mm, fa = [], [], [], [], []
    for w in hold:
        m = wbin == w
        if m.sum() < 30:
            continue
        Aw = cmedian(frames[m])
        dr = np.sqrt(np.mean(np.abs(Aw - A_frozen) ** 2)) / max(sig_mean, 1e-12)
        n_fa = int(np.sum(np.abs(Aw - A_frozen) > SIGMA_K * sigma_frozen))
        dph = np.angle(Aw[REF_TAP]) - fp_ph_frozen
        dph = (dph + np.pi) % (2 * np.pi) - np.pi          # wrap to [-pi,pi]
        wt_h.append(float((w * WIN_MS) / 3.6e6))
        drift.append(float(dr))
        fp_amp_d.append(float((np.abs(Aw[REF_TAP]) / fp_amp_frozen - 1.0) * 100.0))
        fp_ph_mm.append(float(dph / (2 * np.pi) * LAMBDA_MM))
        fa.append(n_fa)
    wt_h = np.array(wt_h); drift = np.array(drift); fa = np.array(fa)
    fp_amp_d = np.array(fp_amp_d); fp_ph_mm = np.array(fp_ph_mm)
    if drift.size == 0:
        return {"listener": listener, "src": hex(src), "status": "no_holdout"}

    over = np.where(drift > 1.0)[0]
    t_1sig = float(wt_h[over[0]]) if over.size else float("inf")
    hold_hours = float(wt_h.max() - wt_h.min() + WIN_MS / 3.6e6) if wt_h.size else 0.0
    fa_per_h = float(fa.sum() / hold_hours) if hold_hours > 0 else float("nan")

    np.savez_compressed(os.path.join(OUT, f"{listener}_{hex(src)}_holdout.npz"),
                        wtime_h=wt_h, drift_sigma=drift, fp_amp_drift_pct=fp_amp_d,
                        fp_phase_drift_mm=fp_ph_mm, false_alarm_taps=fa)
    return {"listener": listener, "src": hex(src), "n": int(n),
            "holdout_windows": int(drift.size),
            "max_drift_sigma": round(float(drift.max()), 3),
            "median_drift_sigma": round(float(np.median(drift)), 3),
            "time_to_1sigma_h": ("inf" if t_1sig == float("inf") else round(t_1sig, 2)),
            "max_fp_amp_drift_pct": round(float(np.max(np.abs(fp_amp_d))), 2),
            "max_fp_phase_drift_mm": round(float(np.max(np.abs(fp_ph_mm))), 3),
            "false_alarm_taps_per_hour": round(fa_per_h, 2),
            "max_false_alarm_taps_in_window": int(fa.max()),
            "status": "ok"}


def main():
    chans = [(l, s) for l in CLEAN for s in TAGS]
    results = []
    with Pool(min(10, len(chans))) as p:
        for r in p.imap_unordered(process, chans):
            results.append(r)
            if r.get("status") == "ok":
                print(f"[Step2v2] {r['listener']}<-{r['src']}: holdout max_drift="
                      f"{r['max_drift_sigma']}sigma t_1sigma={r['time_to_1sigma_h']}h "
                      f"FP_amp_drift<{r['max_fp_amp_drift_pct']}% FP_phase<{r['max_fp_phase_drift_mm']}mm "
                      f"FA/h={r['false_alarm_taps_per_hour']}", flush=True)
    ok = [r for r in results if r.get("status") == "ok"]
    keys = ["listener", "src", "n", "holdout_windows", "max_drift_sigma",
            "median_drift_sigma", "time_to_1sigma_h", "max_fp_amp_drift_pct",
            "max_fp_phase_drift_mm", "false_alarm_taps_per_hour",
            "max_false_alarm_taps_in_window"]
    with open(os.path.join(OUT, "drift_summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore"); w.writeheader()
        for r in sorted(ok, key=lambda x: (x["listener"], x["src"])):
            w.writerow(r)

    # --- figures ---
    fig1, ax1 = plt.subplots(figsize=(12, 6))
    fig2, ax2 = plt.subplots(figsize=(12, 6))
    for i, r in enumerate(sorted(ok, key=lambda x: (x["listener"], x["src"]))):
        d = np.load(os.path.join(OUT, f"{r['listener']}_{r['src']}_holdout.npz"))
        lab = f"{r['listener']}<-{r['src']}"
        ax1.plot(d["wtime_h"], d["drift_sigma"], color=COLORS[i], lw=1.0, label=lab)
        ax2.plot(d["wtime_h"], d["false_alarm_taps"], color=COLORS[i], lw=1.0, label=lab)
    ax1.axhline(1.0, color="k", ls="--", lw=1.4, label="1σ shelf-life threshold")
    ax1.set(xlabel="hours since power-on (HOLDOUT: template frozen on first 30 min)",
            ylabel="drift_rms (σ of frozen noise floor)",
            title="Step 2 v2 — template drift vs FROZEN first-30-min template (proper holdout)")
    ax1.legend(fontsize=6, ncol=3, loc="upper left"); ax1.grid(alpha=0.3)
    fig1.tight_layout(); fig1.savefig(os.path.join(OUT, "drift_curves_holdout.png"), dpi=150)
    ax2.axhline(0, color="k", lw=0.5)
    ax2.set(xlabel="hours since power-on (holdout)",
            ylabel="false-alarm taps ( |A_win - A_frozen| > 5σ_frozen )",
            title="Step 2 v2 — background-subtraction false alarms vs frozen template")
    ax2.legend(fontsize=6, ncol=3, loc="upper left"); ax2.grid(alpha=0.3)
    fig2.tight_layout(); fig2.savefig(os.path.join(OUT, "false_alarm_rate.png"), dpi=150)
    plt.close("all")

    mx = [r["max_drift_sigma"] for r in ok]
    never = [r for r in ok if r["time_to_1sigma_h"] == "inf"]
    fa_all = [r["false_alarm_taps_per_hour"] for r in ok]
    if never:
        verdict = (f"{len(never)}/{len(ok)} channels stay < 1σ across the full holdout "
                   f"(worst {max(mx):.2f}σ) -> template shelf-life > 10 h PROVEN on holdout")
    else:
        worst_t = min(r["time_to_1sigma_h"] for r in ok if r["time_to_1sigma_h"] != "inf")
        verdict = (f"template crosses 1σ; earliest shelf-life {worst_t} h "
                   f"(worst-case channel)")
    summary = {"n_channels": len(ok), "holdout_definition": "freeze first 30min, test 30min..10.5h",
               "max_drift_sigma_median": round(float(np.median(mx)), 3),
               "max_drift_sigma_worst": round(float(max(mx)), 3),
               "channels_never_exceed_1sigma": len(never),
               "false_alarm_taps_per_hour_median": round(float(np.median(fa_all)), 2),
               "false_alarm_taps_per_hour_worst": round(float(max(fa_all)), 2),
               "verdict": verdict}
    json.dump(summary, open(os.path.join(OUT, "stability_v2_summary.json"), "w"), indent=2)
    print(f"\n[Step2v2] {verdict}")
    print(f"[Step2v2] max-drift median={np.median(mx):.2f} worst={max(mx):.2f}σ | "
          f"false-alarm taps/h median={np.median(fa_all):.2f} worst={max(fa_all):.2f}")
    print(f"[Step2v2] wrote {OUT}/ (drift_summary.csv, 2 figures, summary.json)")


if __name__ == "__main__":
    main()
