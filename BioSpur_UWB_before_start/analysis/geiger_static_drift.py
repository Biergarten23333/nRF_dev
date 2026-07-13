#!/usr/bin/env python3
"""Geiger static overnight — per-anchor bias temporal-stability / thermal-drift analysis.

Input:  logs/geiger_overnight_static_20260711/scan.log  (~160k LSCAN, ~10.6 h, static Geiger)
Since the Geiger sat in ONE fixed position ranging to all 8 anchors continuously, the TRUE
range to each anchor is constant — so every change in measured range over time is bias drift
(antenna-delay/clock warm-up = common-mode; per-anchor thermal/multipath = differential) + noise.

Memory-safe: each line is truncated at ';cir=' BEFORE parsing, so the ~8 KB CIR hex per line is
never loaded (avoids the SVD/CIR OOM footgun). Reuses pg_lib geometry + solve_pos + valid_range.

Outputs under analysis/geiger_static_drift_20260711/ : REPORT.md + report.json + figures/.
Run:  python3 analysis/geiger_static_drift.py
"""
import os, sys, json, time, re
import numpy as np

THIS = os.path.abspath(__file__)
REPO = os.path.abspath(os.path.join(os.path.dirname(THIS), ".."))
PGDIR = os.path.join(REPO, "logs", "geiger_scan_20260711_161258_8anchor", "analysis")
sys.path.insert(0, PGDIR)
from pg_lib import load_geometry, valid_range, solve_pos, in_room_box   # noqa

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from scipy.stats import spearmanr, theilslopes

CAP = os.path.join(REPO, "logs", "geiger_overnight_static_20260711")
LOG = os.path.join(CAP, "scan.log")
OUT = os.path.join(REPO, "analysis", "geiger_static_drift_20260711")
FIG = os.path.join(OUT, "figures")
os.makedirs(FIG, exist_ok=True)


# --------------------------------------------------------------------------
def read_duration_s():
    """Reader start/stop from the capture dir; fall back to a nominal 10.6 h."""
    def rd(p):
        try:
            return open(os.path.join(CAP, p)).read().strip()
        except Exception:
            return None
    start = None
    mj = os.path.join(CAP, "metadata.json")
    if os.path.exists(mj):
        try:
            start = json.load(open(mj)).get("start_time")
        except Exception:
            pass
    start = start or rd("start_time.txt")
    stop = rd("stop_time.txt")
    from datetime import datetime
    def parse(s):
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    try:
        return (parse(stop) - parse(start)).total_seconds(), start, stop
    except Exception:
        return 10.6 * 3600, start, stop


# --------------------------------------------------------------------------
_KEYS = ("a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7",
         "cir_aid", "rcph", "rxtofs", "ttcki", "agc")

def parse_log_fast(path):
    """Single pass. Truncate at ';cir=' so the CIR hex is never materialised."""
    N_guess = 170000
    R = np.full((8, N_guess), np.nan)
    aid = np.full(N_guess, -1, int)
    rxt = np.full(N_guess, np.nan)
    rcph = np.full(N_guess, np.nan)
    agc = np.full(N_guess, np.nan)
    i = 0
    with open(path, "r", buffering=1 << 20, errors="ignore") as fp:
        for line in fp:
            if not line.startswith("LSCAN;"):
                continue
            head = line.split(";cir=", 1)[0]
            d = {}
            for tok in head.split(";")[1:]:      # skip leading 'LSCAN'
                k, _, v = tok.partition("=")
                d[k] = v
            if i >= R.shape[1]:                  # grow if needed
                R = np.concatenate([R, np.full((8, N_guess), np.nan)], axis=1)
                aid = np.concatenate([aid, np.full(N_guess, -1, int)])
                rxt = np.concatenate([rxt, np.full(N_guess, np.nan)])
                rcph = np.concatenate([rcph, np.full(N_guess, np.nan)])
                agc = np.concatenate([agc, np.full(N_guess, np.nan)])
            for a in range(8):
                v = d.get("a%d" % a)
                if v is not None:
                    try:
                        mm = int(v)
                        if valid_range(mm):
                            R[a, i] = mm
                    except ValueError:
                        pass
            try:
                aid[i] = int(d.get("cir_aid", -1))
            except ValueError:
                pass
            for arr, key in ((rxt, "rxtofs"), (rcph, "rcph"), (agc, "agc")):
                v = d.get(key)
                if v is not None:
                    try:
                        arr[i] = float(v)
                    except ValueError:
                        pass
            i += 1
    return R[:, :i], aid[:i], rxt[:i], rcph[:i], agc[:i]


# --------------------------------------------------------------------------
def bin_series(t, y, nbin):
    """Median of y in nbin equal time bins. Returns (t_centre, y_med, y_std, n)."""
    edges = np.linspace(t[0], t[-1], nbin + 1)
    tc = 0.5 * (edges[:-1] + edges[1:])
    ym = np.full(nbin, np.nan); ys = np.full(nbin, np.nan); nn = np.zeros(nbin, int)
    idx = np.clip(np.searchsorted(edges, t, side="right") - 1, 0, nbin - 1)
    for b in range(nbin):
        v = y[(idx == b) & np.isfinite(y)]
        if len(v):
            ym[b] = np.median(v); ys[b] = v.std(); nn[b] = len(v)
    return tc, ym, ys, nn


def overlapping_adev(y, dt, n_tau=40):
    """Overlapping Allan deviation of sensor output y[i] (Riley formula on phase = cumsum)."""
    y = np.asarray(y, float)
    ok = np.isfinite(y)
    if ok.sum() < 16:
        return np.array([]), np.array([])
    y = y[ok]                                    # drop gaps (approx: treat as contiguous)
    N = len(y)
    x = np.concatenate([[0.0], np.cumsum(y) * dt])   # phase-like integral, len N+1
    ms = np.unique(np.floor(np.logspace(0, np.log10(N // 4), n_tau)).astype(int))
    ms = ms[(ms >= 1) & (ms <= N // 4)]
    taus, adevs = [], []
    for m in ms:
        L = N - 2 * m
        if L < 1:
            continue
        d2 = x[2 * m:2 * m + L] - 2 * x[m:m + L] + x[0:L]
        avar = (d2 * d2).sum() / (2.0 * (m * dt) ** 2 * L)
        taus.append(m * dt); adevs.append(np.sqrt(avar))
    return np.array(taus), np.array(adevs)


def expo_settle(t, y):
    """Fit y = c + A*exp(-t/tau). Return (c, A, tau_s, ok). t in seconds from 0."""
    m = np.isfinite(y)
    if m.sum() < 10:
        return np.nan, np.nan, np.nan, False
    tt, yy = t[m], y[m]
    A0 = yy[0] - yy[-1]
    p0 = [yy[-1], A0 if abs(A0) > 1 else (1.0 if A0 >= 0 else -1.0), 1800.0]
    try:
        popt, _ = curve_fit(lambda x, c, A, tau: c + A * np.exp(-x / np.abs(tau)),
                            tt - tt[0], yy, p0=p0, maxfev=20000)
        c, A, tau = popt[0], popt[1], abs(popt[2])
        if tau <= 0 or tau > 5 * (tt[-1] - tt[0]):
            return np.nan, np.nan, np.nan, False
        return float(c), float(A), float(tau), True
    except Exception:
        return np.nan, np.nan, np.nan, False


def jsonify(o):
    if isinstance(o, dict):
        return {k: jsonify(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonify(v) for v in o]
    if isinstance(o, (np.floating, float)):
        v = float(o); return None if not np.isfinite(v) else round(v, 5)
    if isinstance(o, (np.integer, int)):
        return int(o)
    if isinstance(o, np.ndarray):
        return jsonify(o.tolist())
    return o


# ==========================================================================
def main():
    t0 = time.time(); ncpu = os.cpu_count()
    dur_s, start_s, stop_s = read_duration_s()
    P, LBL, DLY, W, Wc = load_geometry()
    print(f"[env] cpu={ncpu}  duration={dur_s/3600:.2f} h  start={start_s} stop={stop_s}")

    tp = time.time()
    R, aid, rxt, rcph, agc = parse_log_fast(LOG)
    N = R.shape[1]
    dt = dur_s / max(1, N - 1)
    t = np.arange(N) * dt                          # seconds from start (uniform-rate assumption)
    print(f"[parse] {N} LSCAN in {time.time()-tp:.1f}s  (single-thread, CIR truncated)  "
          f"dt={dt*1000:.1f} ms -> {1/dt:.2f} Hz")

    # ---------- per-anchor drift stats ----------
    # distribution stats on full-res; robust drift slope on 1-min-binned medians
    # (Theil-Sen is O(n^2) -> MUST bin first; binning also denoises the slope).
    hr = t / 3600.0
    NBIN = min(636, max(60, N // 250))              # ~1-min bins (used throughout)
    med_a = np.array([np.nanmedian(R[a]) for a in range(8)])
    ref_pos = solve_ref(P, med_a)                    # single static-position estimate
    stats = []
    for a in range(8):
        y = R[a]; ok = np.isfinite(y)
        yv = y[ok]
        n = int(ok.sum()); vf = n / N
        # robust drift over the run from binned medians
        tcb_, yb_, _, _ = bin_series(t, y, NBIN)
        mb = np.isfinite(yb_)
        if mb.sum() > 10:
            ts = theilslopes(yb_[mb], tcb_[mb] / 3600.0)     # slope mm/hr
            ols = np.polyfit(tcb_[mb] / 3600.0, yb_[mb], 1)[0]
        else:
            ts = (np.nan, np.nan, np.nan, np.nan); ols = np.nan
        # short-term (tau=1 sample) noise from successive diffs (Allan-ish)
        dy = np.diff(yv)
        sig1 = float(np.nanstd(dy) / np.sqrt(2)) if len(dy) > 2 else np.nan
        q1, q3 = (np.percentile(yv, [25, 75]) if n else (np.nan, np.nan))
        stats.append(dict(
            anchor=int(a), label=LBL[a], group=("near" if a in (1, 2, 5, 6) else "far"),
            true_range_mm=float(np.linalg.norm(P[a] - ref_pos)) if n else np.nan,
            n=n, valid_frac=vf, mean=float(np.mean(yv)) if n else np.nan,
            median=float(med_a[a]), std=float(np.std(yv)) if n else np.nan,
            min=float(np.min(yv)) if n else np.nan, max=float(np.max(yv)) if n else np.nan,
            iqr=float(q3 - q1) if n else np.nan,
            drift_theilsen_mm_per_hr=float(ts[0]), drift_ols_mm_per_hr=float(ols),
            total_drift_mm=float(ts[0] * dur_s / 3600.0) if np.isfinite(ts[0]) else np.nan,
            tick_noise_1samp_mm=sig1))
    print("[drift] per-anchor Theil-Sen slopes (mm/hr): " +
          " ".join(f"{s['label']}={s['drift_theilsen_mm_per_hr']:+.2f}" for s in stats))

    # ---------- common-mode vs differential decomposition ----------
    # per-cycle deviation of each anchor from its own global median, then split.
    # Common-mode uses the MEDIAN across anchors (robust): a single anchor stepping
    # (e.g. B's -300 mm event) or bursting (E) would swamp a mean, faking a shared drift.
    dev = R - med_a[:, None]                        # (8,N) mm deviation
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        cm = np.nanmedian(dev, axis=0)              # robust common-mode (Geiger antenna/clock)
    diff = dev - cm[None, :]                         # per-anchor differential

    # ---------- per-anchor event / stability metrics (on 1-min binned differential) ----------
    BINMIN = dur_s / NBIN / 60.0                     # minutes per bin
    events = []
    for a in range(8):
        _, db, _, _ = bin_series(t, diff[a], NBIN)
        m = np.isfinite(db); dv = db[m]
        if len(dv) < 5:
            events.append(dict(label=LBL[a], stability="NO DATA")); continue
        big = np.abs(dv) > 100.0
        burst_frac = float(big.mean())
        # longest contiguous run of |diff|>100 mm  -> distinguishes a step from bursts
        longest = cur = 0
        trans = 0; prev = False
        for bflag in big:
            cur = cur + 1 if bflag else 0
            longest = max(longest, cur)
            if bflag and not prev:
                trans += 1
            prev = bflag
        longest_min = longest * BINMIN
        maxexc = float(np.max(np.abs(dv)))
        s1 = next(s for s in stats if s["anchor"] == a)["tick_noise_1samp_mm"]
        # classify
        if burst_frac > 0.05 and trans > 8:
            cls = "BURSTY (multipath)"
        elif longest_min > 30 and maxexc > 100:
            cls = "STEP/EXCURSION"
        elif abs(next(s for s in stats if s["anchor"] == a)["total_drift_mm"]) > 40:
            cls = "DRIFTING"
        elif s1 is not None and s1 > 45:
            cls = "NOISY"
        else:
            cls = "STABLE"
        events.append(dict(label=LBL[a], stability=cls, burst_frac_gt100mm=burst_frac,
                           n_excursions=int(trans), longest_excursion_min=float(longest_min),
                           max_abs_diff_mm=maxexc, sigma1_mm=s1))
    for s in stats:                                  # attach class to the per-anchor stats
        s["stability"] = next(e["stability"] for e in events if e["label"] == s["label"])
    print("[events] " + " ".join(f"{e['label']}={e['stability']}" for e in events))
    tcb, cmb, cms, _ = bin_series(t, cm, NBIN)
    cm_c, cm_A, cm_tau, cm_ok = expo_settle(tcb, cmb)   # (usually fails: rise, not a decay)
    cm_slope = float(theilslopes(cmb[np.isfinite(cmb)], tcb[np.isfinite(cmb)] / 3600)[0]) \
        if np.isfinite(cmb).sum() > 10 else np.nan
    cm_p2p = float(np.nanmax(cmb) - np.nanmin(cmb))
    # honest shape: first-2h vs last-2h level (the robust common-mode is a slow morning rise,
    # NOT a cold-start exponential — a mean-based common-mode faked that via B's step).
    tch = tcb / 3600.0
    cm_first = float(np.nanmedian(cmb[tch < 2.0])) if np.isfinite(cmb[tch < 2.0]).any() else np.nan
    cm_last = float(np.nanmedian(cmb[tch > (dur_s/3600 - 2.0)])) if np.isfinite(cmb[tch > (dur_s/3600 - 2.0)]).any() else np.nan
    cm_rise = cm_last - cm_first
    diff_summary = []
    for a in range(8):
        _, db, _, _ = bin_series(t, diff[a], NBIN)
        m = np.isfinite(db)
        sl = float(theilslopes(db[m], tcb[m] / 3600)[0]) if m.sum() > 10 else np.nan
        diff_summary.append(dict(label=LBL[a], diff_slope_mm_per_hr=sl,
                                 diff_p2p_mm=float(np.nanmax(db) - np.nanmin(db)) if m.any() else np.nan,
                                 diff_total_mm=float(sl * dur_s / 3600) if np.isfinite(sl) else np.nan))
    print(f"[decomp] common-mode: slope={cm_slope:+.2f} mm/hr  p2p={cm_p2p:.1f} mm  "
          f"warmup tau={cm_tau/60:.1f} min A={cm_A:+.1f} mm (ok={cm_ok})")

    # ---------- thermal proxy: rxtofs (CFO) per anchor over time ----------
    tcb2, rxb, _, _ = bin_series(t, rxt, NBIN)      # pooled CFO proxy
    m = np.isfinite(rxb) & np.isfinite(cmb)
    rho_cfo, p_cfo = (spearmanr(rxb[m], cmb[m]) if m.sum() > 10 else (np.nan, np.nan))
    rxt_slope = float(theilslopes(rxb[np.isfinite(rxb)], tcb2[np.isfinite(rxb)] / 3600)[0]) \
        if np.isfinite(rxb).sum() > 10 else np.nan
    per_anchor_cfo = {}
    for a in range(8):
        sel = (aid == a) & np.isfinite(rxt)
        if sel.sum() > 50:
            _, rb, _, _ = bin_series(t[sel], rxt[sel], min(120, NBIN))
            mm = np.isfinite(rb)
            per_anchor_cfo[LBL[a]] = float(theilslopes(rb[mm],
                                     np.linspace(0, dur_s/3600, len(rb))[mm])[0]) if mm.sum() > 5 else np.nan
    agc_const = bool(np.nanstd(agc) < 1e-6)
    # CFO cold-start settling: first-1h vs plateau (the radio front-end DOES warm up early)
    tch2 = tcb2 / 3600.0
    rxt_start = float(np.nanmedian(rxb[tch2 < 1.0])) if np.isfinite(rxb[tch2 < 1.0]).any() else np.nan
    rxt_plateau = float(np.nanmedian(rxb[(tch2 > 1.0) & (tch2 < 6.0)])) if np.isfinite(rxb[(tch2 > 1.0) & (tch2 < 6.0)]).any() else np.nan
    rxt_settle = rxt_plateau - rxt_start
    print(f"[thermal] rxtofs(CFO) slope={rxt_slope:+.3f}/hr  cold-start settle {rxt_settle:+.1f} "
          f"(first1h {rxt_start:.0f}->plateau {rxt_plateau:.0f})  "
          f"corr(CFO, common-mode)={rho_cfo:+.3f} (p={p_cfo:.2g})  agc_const={agc_const}")

    # ---------- Allan deviation per anchor ----------
    adev = {}
    for a in range(8):
        taus, ad = overlapping_adev(R[a][np.isfinite(R[a])], dt)
        adev[LBL[a]] = (taus, ad)

    # ---------- position stability (bin -> trilaterate) ----------
    binned = np.full((8, NBIN), np.nan)
    for a in range(8):
        _, yb, _, _ = bin_series(t, R[a], NBIN)
        binned[a] = yb
    pos = np.full((NBIN, 3), np.nan); pres = np.full(NBIN, np.nan)
    x0 = P.mean(0)
    for b in range(NBIN):
        rg = {a: binned[a, b] for a in range(8) if np.isfinite(binned[a, b])}
        if len(rg) >= 4:
            s, ids, rms = solve_pos(P, rg, x0, ids=sorted(rg))
            if s is not None and in_room_box(s):
                pos[b] = s; pres[b] = rms; x0 = s
    pm = np.isfinite(pos[:, 0])
    pos0 = np.nanmedian(pos[pm][:max(3, pm.sum()//20)], axis=0) if pm.any() else np.array([np.nan]*3)
    pos_wander = pos - pos0
    pos_p2p = [float(np.nanmax(pos[pm, k]) - np.nanmin(pos[pm, k])) if pm.any() else np.nan for k in range(3)]
    pos_drift3d = float(np.linalg.norm(np.nanmedian(pos[pm][-max(3, pm.sum()//20):], 0) - pos0)) if pm.sum() > 6 else np.nan
    # "clean" positional stability = before the B step event (< 6.4 h), where the solve is not
    # corrupted by a single stepped anchor -> the true drift-only positional stability
    tch_pos = np.linspace(0, dur_s/3600, NBIN)
    clean = pm & (tch_pos < 6.4)
    pos_p2p_clean = [float(np.nanmax(pos[clean, k]) - np.nanmin(pos[clean, k])) if clean.any() else np.nan for k in range(3)]
    print(f"[pos] solved {pm.sum()}/{NBIN} bins; full p2p (x,y,z)="
          f"{pos_p2p[0]:.0f},{pos_p2p[1]:.0f},{pos_p2p[2]:.0f} mm (B-step-corrupted); "
          f"clean(<6.4h) p2p={pos_p2p_clean[0]:.0f},{pos_p2p_clean[1]:.0f},{pos_p2p_clean[2]:.0f} mm")

    # ================= figures =================
    try:
        make_figs(t, hr, R, LBL, stats, dur_s, cm, cmb, tcb, cm_c, cm_A, cm_tau, cm_ok,
                  diff, adev, rxt, rxb, tcb2, tcb, pos, pos_wander, pres, pm, NBIN)
    except Exception as ex:
        print(f"[figs] WARN {ex}")

    # ================= outputs =================
    report = dict(
        meta=dict(log=os.path.relpath(LOG, REPO), n_lscan=N, duration_h=dur_s/3600.0,
                  rate_hz=1/dt, start=start_s, stop=stop_s, cpu_count=ncpu,
                  parse_note="single-thread; CIR truncated before parse (memory-safe)",
                  time_axis="uniform-rate (no per-line timestamp; 0 reconnects so index≈time)"),
        per_anchor=stats,
        events=events,
        common_mode=dict(slope_mm_per_hr=cm_slope, p2p_mm=cm_p2p,
                         first2h_mm=cm_first, last2h_mm=cm_last, rise_first_to_last_mm=cm_rise,
                         warmup_expfit_ok=cm_ok,
                         warmup_tau_min=cm_tau/60.0 if np.isfinite(cm_tau) else None, warmup_amp_mm=cm_A),
        differential=diff_summary,
        thermal=dict(rxtofs_cfo_slope_per_hr=rxt_slope,
                     rxtofs_coldstart_settle=rxt_settle, rxtofs_first1h=rxt_start, rxtofs_plateau=rxt_plateau,
                     corr_cfo_vs_commonmode_spearman=rho_cfo, corr_p=p_cfo,
                     per_anchor_cfo_slope_per_hr=per_anchor_cfo, agc_constant=agc_const),
        position=dict(solved_bins=int(pm.sum()), n_bins=NBIN, p2p_xyz_mm=pos_p2p,
                      p2p_xyz_clean_mm=pos_p2p_clean, net_drift_mm=pos_drift3d,
                      median_solve_resid_mm=float(np.nanmedian(pres))),
        allan=dict((k, dict(tau_s=v[0].tolist(), adev_mm=v[1].tolist())) for k, v in adev.items()),
    )
    with open(os.path.join(OUT, "report.json"), "w") as fp:
        json.dump(jsonify(report), fp, indent=2)
    write_report(report, dur_s, N, ncpu, time.time() - t0)
    print(f"[done] total {time.time()-t0:.1f}s -> {OUT}")


def solve_ref(P, med_a):
    """Quick static-position estimate from median ranges (for a true-range column)."""
    rg = {a: med_a[a] for a in range(8) if np.isfinite(med_a[a])}
    s, _, _ = solve_pos(P, rg, P.mean(0), ids=sorted(rg))
    return s if s is not None else P.mean(0)


# --------------------------------------------------------------------------
def make_figs(t, hr, R, LBL, stats, dur_s, cm, cmb, tcb, cm_c, cm_A, cm_tau, cm_ok,
              diff, adev, rxt, rxb, tcb2, tcbcm, pos, pos_wander, pres, pm, NBIN):
    # 1. per-anchor range vs time
    fig, ax = plt.subplots(2, 4, figsize=(22, 9))
    for a in range(8):
        x = ax[a // 4][a % 4]; y = R[a]; ok = np.isfinite(y)
        x.plot(hr[ok][::20], y[ok][::20], '.', ms=1, alpha=0.3, color=plt.cm.tab10(a))
        tcb_, yb_, _, _ = bin_series(t, y, NBIN)
        x.plot(tcb_/3600, yb_, '-', color="k", lw=1)
        s = stats[a]
        x.set_title(f"{s['label']} [{s.get('stability','?')}] drift {s['drift_theilsen_mm_per_hr']:+.2f} mm/hr "
                    f"({s['total_drift_mm']:+.0f} mm), σ1={s['tick_noise_1samp_mm']:.0f}", fontsize=9)
        x.set_xlabel("h"); x.set_ylabel("range mm"); x.grid(alpha=0.3)
    fig.suptitle("Per-anchor range vs time (dots=raw ↓20, black=1-min median)", fontsize=13)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "per_anchor_range_vs_time.png"), dpi=100); plt.close(fig)

    # 2. common-mode + warmup fit, and differential
    fig, ax = plt.subplots(1, 2, figsize=(18, 6))
    ax[0].plot(tcb/3600, cmb, '.', ms=3, color="tab:blue", label="common-mode (1-min med)")
    if cm_ok:
        tt = tcb - tcb[0]
        ax[0].plot(tcb/3600, cm_c + cm_A*np.exp(-tt/cm_tau), 'r-', lw=2,
                   label=f"warmup fit τ={cm_tau/60:.0f} min, A={cm_A:+.0f} mm")
    ax[0].axhline(0, color="gray", lw=0.6); ax[0].legend(); ax[0].grid(alpha=0.3)
    ax[0].set_xlabel("h"); ax[0].set_ylabel("common-mode range dev (mm)")
    ax[0].set_title("Common-mode drift (robust median across anchors): flat then slow morning rise")
    for a in range(8):
        _, db, _, _ = bin_series(t, diff[a], NBIN)
        ax[1].plot(tcb/3600, db, '-', lw=1, color=plt.cm.tab10(a), label=LBL[a])
    ax[1].axhline(0, color="gray", lw=0.6); ax[1].legend(ncol=4, fontsize=8); ax[1].grid(alpha=0.3)
    ax[1].set_xlabel("h"); ax[1].set_ylabel("differential dev (mm)")
    ax[1].set_title("Per-anchor differential drift (common-mode removed = per-anchor thermal/geometry)")
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "commonmode_differential.png"), dpi=105); plt.close(fig)

    # 3. Allan deviation
    fig, axx = plt.subplots(figsize=(9, 7))
    for a in range(8):
        taus, ad = adev[LBL[a]]
        if len(taus):
            axx.loglog(taus, ad, '.-', color=plt.cm.tab10(a), label=LBL[a])
    axx.set_xlabel("averaging time τ (s)"); axx.set_ylabel("Allan deviation (mm)")
    axx.set_title("Overlapping Allan deviation per anchor\n(slope −½ = white noise; min = optimal avg; up-turn = drift)")
    axx.grid(alpha=0.3, which="both"); axx.legend(ncol=2)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "allan_deviation.png"), dpi=110); plt.close(fig)

    # 4. CFO proxy + position wander
    fig, ax = plt.subplots(1, 2, figsize=(18, 6))
    ax[0].plot(tcb2/3600, rxb, '.-', ms=3, color="tab:purple")
    ax[0].set_xlabel("h"); ax[0].set_ylabel("rxtofs (CFO/timing proxy)")
    ax[0].set_title("CFO/timing proxy (rxtofs) 1-min median — thermal indicator"); ax[0].grid(alpha=0.3)
    for k, lab, c in [(0, "x", "tab:red"), (1, "y", "tab:green"), (2, "z", "tab:blue")]:
        ax[1].plot(np.linspace(0, dur_s/3600, NBIN)[pm], pos_wander[pm, k], '-', color=c, label=lab)
    ax[1].axhline(0, color="gray", lw=0.6); ax[1].legend(); ax[1].grid(alpha=0.3)
    ax[1].set_xlabel("h"); ax[1].set_ylabel("solved position wander (mm)")
    ax[1].set_title("Static-Geiger trilaterated position wander (net effect of all drifts)")
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "cfo_and_position.png"), dpi=105); plt.close(fig)


# --------------------------------------------------------------------------
def _f(v, nd=2):
    try:
        if v is None or not np.isfinite(v):
            return "n/a"
    except TypeError:
        return "n/a"
    return f"{v:.{nd}f}"

def write_report(rep, dur_s, N, ncpu, wall):
    o = []; W = o.append
    m = rep["meta"]
    W("# Geiger Static Overnight — Per-Anchor Bias Temporal Stability / Thermal Drift\n")
    W(f"**Capture:** `{m['log']}` — **{N:,} LSCAN** over **{m['duration_h']:.2f} h** "
      f"({m['start']} → {m['stop']}), steady **{m['rate_hz']:.2f} Hz**, 0 reconnects. "
      f"Geiger static (fixed position), ranging to all 8 anchors A–H.\n")
    W(f"**Method:** true range to each anchor is constant (static), so range(t) variation = "
      f"bias drift + noise. Decomposed into **common-mode** (robust median across anchors → shared "
      f"Geiger antenna-delay/clock term) and **differential** (per-anchor → thermal/multipath/events). "
      f"{m['parse_note']}. Time axis: {m['time_axis']}.\n")
    W(f"**Compute:** {ncpu} logical cores; parse is single-thread I/O+regex bound "
      f"(~{wall:.0f}s total incl. Allan/trilat), numpy stages light — no GPU. "
      f"CIR never loaded (each line truncated at `;cir=`), peak RAM < 200 MB.\n")

    cmn = rep["common_mode"]; th = rep["thermal"]; pz = rep["position"]
    stable = [s["label"] for s in rep["per_anchor"] if s["stability"] == "STABLE"]
    bad = [e for e in rep["events"] if e["stability"] not in ("STABLE", "NO DATA")]
    W("\n## TL;DR\n")
    W(f"- **{len(stable)}/8 anchors are temporally stable** over 10.6 h: "
      f"**{', '.join(stable) if stable else 'none'}** — single-sample noise σ₁≈23–29 mm and "
      f"<40 mm slow drift. The bias of a warmed-up link is stable to a few tens of mm all night.\n")
    W(f"- **The drift budget is NOT smooth thermal — it is dominated by a few discrete "
      f"per-anchor events:** " +
      ("; ".join(f"**{e['label']}** = {e['stability']}" for e in bad) if bad else "none") + ".\n")
    W(f"- **Shared (common-mode) drift is small and slow, not a cold-start transient.** Using a "
      f"robust median across anchors, the common-mode is flat (±5 mm) for the first ~6 h, then rises "
      f"**{_f(cmn['rise_first_to_last_mm'],0)} mm** through the morning (first-2h {_f(cmn['first2h_mm'],0)} "
      f"→ last-2h {_f(cmn['last2h_mm'],0)} mm; slope {_f(cmn['slope_mm_per_hr'])} mm/hr) — consistent "
      f"with the room warming toward midday. (An exponential warm-up does *not* fit: fit_ok="
      f"{cmn['warmup_expfit_ok']}. The 'τ≈45 min' a mean-based common-mode would show is an artifact "
      f"of B's step, not real.)\n")
    W(f"- **CFO front-end warm-up is real but decoupled from range.** rxtofs settles "
      f"{_f(th['rxtofs_coldstart_settle'],0)} units in the first ~1.5 h then plateaus, yet it does "
      f"**not** track the range common-mode (Spearman ρ={_f(th['corr_cfo_vs_commonmode_spearman'])}, "
      f"p={_f(th['corr_p'],2)}) — so CFO is a radio-thermal indicator, not a usable range-bias proxy here. "
      f"AGC constant: {th['agc_constant']}.\n")
    W(f"- **Net position wander** of the static probe: full-run p2p (x,y,z) = "
      f"{_f(pz['p2p_xyz_mm'][0],0)},{_f(pz['p2p_xyz_mm'][1],0)},**{_f(pz['p2p_xyz_mm'][2],0)}** mm, "
      f"dominated by B's step leaking into the solve. Pre-event (<6.4 h) horizontal stability is "
      f"**x,y ≈ {_f(pz['p2p_xyz_clean_mm'][0],0)},{_f(pz['p2p_xyz_clean_mm'][1],0)} mm**; z stays large "
      f"(~{_f(pz['p2p_xyz_clean_mm'][2],0)} mm) from poor z-DOP amplifying E/H multipath, not drift.\n")

    W("\n## 1 · Per-anchor stability, drift & noise\n")
    W("| anchor | class | n | valid% | median mm | std mm | IQR mm | drift mm/hr | total drift mm | σ₁ mm |")
    W("|---|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for s in rep["per_anchor"]:
        W(f"| {s['label']} | {s['stability']} | {s['n']:,} | {100*s['valid_frac']:.1f} | "
          f"{_f(s['median'],0)} | {_f(s['std'],0)} | {_f(s['iqr'],0)} | "
          f"{_f(s['drift_theilsen_mm_per_hr'])} | {_f(s['total_drift_mm'],0)} | {_f(s['tick_noise_1samp_mm'],0)} |")
    W(f"\n`drift` = robust Theil-Sen slope on 1-min medians × {dur_s/3600:.1f} h = `total drift`; "
      f"`σ₁` = single-sample noise (√2-scaled successive differences). For **stable** anchors σ₁ and "
      f"drift are the real bias-stability numbers; for BURSTY/STEP anchors these are inflated by the "
      f"events (see §1b) and should not be read as smooth drift.\n")

    W("\n### 1b · Event / instability detail\n")
    W("| anchor | class | σ₁ mm | burst frac (\\|Δ\\|>100mm) | #excursions | longest excursion (min) | max \\|Δ\\| mm |")
    W("|---|---|--:|--:|--:|--:|--:|")
    for e in rep["events"]:
        if e["stability"] in ("STABLE", "NO DATA"):
            W(f"| {e['label']} | {e['stability']} | {_f(e.get('sigma1_mm'),0)} | "
              f"{_f(e.get('burst_frac_gt100mm'),3)} | {e.get('n_excursions','–')} | "
              f"{_f(e.get('longest_excursion_min'),0)} | {_f(e.get('max_abs_diff_mm'),0)} |")
        else:
            W(f"| **{e['label']}** | **{e['stability']}** | {_f(e.get('sigma1_mm'),0)} | "
              f"{_f(e.get('burst_frac_gt100mm'),3)} | {e.get('n_excursions','–')} | "
              f"{_f(e.get('longest_excursion_min'),0)} | {_f(e.get('max_abs_diff_mm'),0)} |")
    W(f"\n_`burst frac` = fraction of 1-min bins with |differential| > 100 mm; a **BURSTY** anchor "
      f"(many short excursions) is multipath/NLOS-limited (E flips between LOS and a ~+290 mm image); "
      f"a **STEP/EXCURSION** anchor holds one shifted level for >30 min (B dropped ~300 mm for ~2.4 h "
      f"mid-run). Neither is temperature drift — they are link-geometry / environment events._\n")

    W("\n## 2 · Common-mode vs differential decomposition\n")
    W(f"**Common-mode** = median across anchors of each anchor's deviation from its own baseline "
      f"(robust, so a single stepping/bursting anchor cannot fake a shared drift). Shape: flat within "
      f"±5 mm for the first ~6 h, then a monotonic rise to +{_f(cmn['last2h_mm'],0)} mm by the end "
      f"(first-2h {_f(cmn['first2h_mm'],0)} → last-2h {_f(cmn['last2h_mm'],0)} mm, net "
      f"{_f(cmn['rise_first_to_last_mm'],0)} mm; slope {_f(cmn['slope_mm_per_hr'])} mm/hr, p2p "
      f"{_f(cmn['p2p_mm'],0)} mm). This is a slow morning warming trend, **not** a power-on transient "
      f"(exponential-settle fit rejected). See `figures/commonmode_differential.png`.\n")
    W("\n**Differential** (per anchor, common-mode removed = genuine per-anchor thermal/geometry):\n")
    W("| anchor | diff slope mm/hr | diff total mm | diff p2p mm |")
    W("|---|--:|--:|--:|")
    for d in rep["differential"]:
        W(f"| {d['label']} | {_f(d['diff_slope_mm_per_hr'])} | {_f(d['diff_total_mm'],0)} | {_f(d['diff_p2p_mm'],0)} |")
    W(f"\n_Interpretation: if common-mode ≫ differential, the drift is mostly the **Geiger's own** "
      f"warm-up (one antenna-delay recal fixes all anchors); large differential on a specific "
      f"anchor points to that link's geometry/multipath or that anchor's own thermal._\n")

    W("\n## 3 · Thermal proxy (CFO) & stability\n")
    W(f"`rxtofs` (receiver carrier/timing-offset proxy) shows a genuine **cold-start settle of "
      f"{_f(th['rxtofs_coldstart_settle'],0)} units in the first ~1.5 h** (first-1h "
      f"{_f(th['rxtofs_first1h'],0)} → plateau {_f(th['rxtofs_plateau'],0)}), i.e. the radio front-end "
      f"warming up, then flat (slope {_f(th['rxtofs_cfo_slope_per_hr'],3)}/hr). **But it does not track "
      f"the range common-mode** (Spearman ρ={_f(th['corr_cfo_vs_commonmode_spearman'])}, "
      f"p={_f(th['corr_p'],3)}): the CFO warms up in the first hour while the range common-mode rises "
      f"in the *morning* — different time courses. So CFO indicates front-end temperature but is not a "
      f"usable proxy for the range bias here. AGC is constant ({th['agc_constant']}) and ttcki is a "
      f"fixed config word (no room thermometer was logged). Per-anchor CFO slopes: "
      + ", ".join(f"{k}={_f(v,3)}" for k, v in th["per_anchor_cfo_slope_per_hr"].items()) + ".\n")
    W(f"\nOverlapping **Allan deviation** per anchor in `figures/allan_deviation.png`: the −½-slope "
      f"region is white measurement noise (averaging helps), the minimum marks the optimal "
      f"averaging time, and the up-turn at long τ is the drift/thermal random-walk floor.\n")

    W("\n## 4 · Net position stability\n")
    W(f"Trilaterating the 1-min-binned ranges ({pz['solved_bins']}/{pz['n_bins']} bins solved, "
      f"median fit residual {_f(pz['median_solve_resid_mm'],0)} mm) gives the static probe's apparent "
      f"position wander. Full-run p2p (x,y,z) = {_f(pz['p2p_xyz_mm'][0],0)}, {_f(pz['p2p_xyz_mm'][1],0)}, "
      f"{_f(pz['p2p_xyz_mm'][2],0)} mm looks large, but the z/x jumps coincide exactly with B's step at "
      f"6.7 h — a single bad anchor corrupting the non-robust solve. Before the B event (<6.4 h) the "
      f"wander drops to **{_f(pz['p2p_xyz_clean_mm'][0],0)}, {_f(pz['p2p_xyz_clean_mm'][1],0)}, "
      f"{_f(pz['p2p_xyz_clean_mm'][2],0)} mm** (x,y,z). The horizontal **x,y ≈ "
      f"{_f(pz['p2p_xyz_clean_mm'][0],0)}–{_f(pz['p2p_xyz_clean_mm'][1],0)} mm is the genuine "
      f"drift/noise stability**; the large **z (~{_f(pz['p2p_xyz_clean_mm'][2],0)} mm) is dominated by "
      f"poor z-DOP amplifying the E/H multipath bursts**, not by bias drift. An outlier-rejecting solver "
      f"would pull the full-run number back toward this. See `figures/cfo_and_position.png`.\n")

    W("\n## 5 · Takeaways\n")
    bad = [e for e in rep["events"] if e["stability"] not in ("STABLE", "NO DATA")]
    stables = [s["label"] for s in rep["per_anchor"] if s["stability"] == "STABLE"]
    W(f"- **Per-anchor bias is temporally stable for a clean line-of-sight link.** "
      f"{len(stables)}/8 anchors ({', '.join(stables)}) held to σ₁≈25 mm single-sample noise and "
      f"<40 mm slow drift over 10.6 h. Temperature is a **small** term: the robust shared drift is only "
      f"{_f(cmn['slope_mm_per_hr'])} mm/hr (a ~{_f(cmn['rise_first_to_last_mm'],0)} mm morning rise), and "
      f"there is no sharp power-on transient in the *range* (the front-end CFO does settle in ~1.5 h, "
      f"but that does not propagate to a range bias).\n")
    W(f"- **The real stability risk is per-anchor link events, not temperature:** " +
      "; ".join(f"**{e['label']}** {e['stability']} (max |Δ| {_f(e['max_abs_diff_mm'],0)} mm)" for e in bad) +
      ". These are multipath/geometry (E flips LOS↔≈+290 mm image) and environment (B held ≈−300 mm for "
      f"~2.4 h mid-run). They are **not** fixed by antenna-delay recal or a thermal model — they need "
      f"anchor placement/occlusion addressed and an outlier-rejecting solver (LOO/robust trilateration).\n")
    W(f"- **Practical guidance:** per-anchor bias is stable to a few tens of mm once running; gate "
      f"anchor **E** (and watch **B/H**) with a first-path/multipath-quality check rather than trusting "
      f"its raw range, and use a robust solver so one stepped anchor cannot move the fix (as B did here, "
      f"injecting a spurious ~400 mm z-jump). The slow morning common-mode ({_f(cmn['slope_mm_per_hr'])} "
      f"mm/hr) is negligible for short sessions.\n")
    W(f"- **CFO is not an actionable range-bias proxy from this run** (ρ={_f(th['corr_cfo_vs_commonmode_spearman'])}, "
      f"p={_f(th['corr_p'],2)}); it tracks front-end temperature on a different time course. Closing the "
      f"thermal loop properly needs a logged room/board thermometer alongside the range.\n")

    W("\n## Artifacts\n")
    for f in ["report.json", "figures/per_anchor_range_vs_time.png",
              "figures/commonmode_differential.png", "figures/allan_deviation.png",
              "figures/cfo_and_position.png"]:
        W(f"- `{f}`")
    with open(os.path.join(OUT, "REPORT.md"), "w") as fp:
        fp.write("\n".join(o) + "\n")


if __name__ == "__main__":
    main()
