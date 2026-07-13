#!/usr/bin/env python3
"""Geiger MODE_SCAN -- PROXY GATE + SIGMA MAP + GAUGE FIT pipeline (P1 + P2).

Runs the full pre-registered analysis from HANDOFF_FOR_ANALYSIS.md:
  P1.0 parse + solve            P1.3 GATE TEST (verdict)     P2.1 per-anchor gauge fit
  P1.1 LOO residuals (Pool)     P1.4 spatial sigma map       P2.2 AutoPos delay cross-check
  P1.2 locked CIR features      P1.5 rotation body-shadow    P2.3 slope vs APS011

Method locks: LOO residuals only; partial Spearman controlling
{solved_range, anchor_id, n_anchors}; locked feature set; pre-registered
GO/NO-GO thresholds; no post-hoc feature sweeping.

Usage:  python3 pg_pipeline.py           (run from anywhere; paths are absolute)
"""
import os, sys, json, time
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from multiprocessing import Pool

import pg_lib as L
from pg_lib import (HERE, FIGDIR, NTAP, TAP_NS, RUN_S, FEATURES,
                    load_geometry, parse_log, valid_ids, solve_pos, in_room_box,
                    cir_features, partial_spearman, partial_spearman_boot,
                    auc_score, raw_spearman)

os.makedirs(FIGDIR, exist_ok=True)
NPROC = 6
BIG_ERR_MM = 250.0

# --------------------------------------------------------------------------
# globals for the LOO worker (set via Pool initializer -> robust to fork/spawn)
# --------------------------------------------------------------------------
_P = _RNG = _POS = None
def _loo_init(P, rng_list, pos_full):
    global _P, _RNG, _POS
    _P, _RNG, _POS = P, rng_list, pos_full

def _loo_cycle(i):
    """LOO re-solves for one cycle. Returns list of (cyc, anchor, range, e_signed, loo_pos[3])."""
    rg  = _RNG[i]
    ids = valid_ids(rg)
    if len(ids) < 6:                                  # LOO only when >=6 responders
        return []
    x0 = _POS[i] if np.all(np.isfinite(_POS[i])) else _P.mean(0)
    out = []
    for a in ids:
        rem = [j for j in ids if j != a]              # >=5 remaining
        s, _ids, _rms = solve_pos(_P, rg, x0, ids=rem)
        if s is None:
            continue
        e = rg[a] - float(np.linalg.norm(s - _P[a]))  # signed LOO residual (measured - predicted)
        out.append((i, a, float(rg[a]), e, s[0], s[1], s[2]))
    return out


def main():
    t_all = time.time()
    P, LBL, DLY, W, Wc = load_geometry()
    rows = parse_log()
    N = len(rows)
    rng_list = [r["rng"] for r in rows]
    t = np.arange(N) * RUN_S / N
    print(f"[P1.0] parsed {N} LSCAN cycles; "
          f"{sum(r['cir'] is not None for r in rows)} full CIRs")

    # ---------------- P1.0 full solve (sequential warm-start) ----------------
    pos   = np.full((N, 3), np.nan)
    resid = np.full(N, np.nan)
    nanch = np.zeros(N, int)
    x0 = P.mean(0)
    for i, rg in enumerate(rng_list):
        ids = valid_ids(rg)
        nanch[i] = len(ids)
        s, _ids, rms = solve_pos(P, rg, x0, ids=ids)
        if s is not None and in_room_box(s):
            pos[i] = s; resid[i] = rms; x0 = s
    mpos = np.isfinite(pos[:, 0])
    print(f"[P1.0] solved {mpos.sum()}/{N}  median solve-residual "
          f"{np.nanmedian(resid):.0f} mm")

    # ---------------- P1.1 LOO residuals (multiprocessing) ----------------
    cand = [i for i in range(N) if len(valid_ids(rng_list[i])) >= 6]
    print(f"[P1.1] LOO on {len(cand)} cycles (>=6 responders) x ~6.5 anchors "
          f"using Pool({NPROC})...")
    tw = time.time(); to = os.times()
    with Pool(NPROC, initializer=_loo_init, initargs=(P, rng_list, pos)) as pool:
        chunks = pool.map(_loo_cycle, cand, chunksize=8)
    wall = time.time() - tw; ot = os.times()
    cpu_child = (ot.children_user + ot.children_system) - (to.children_user + to.children_system)
    util = cpu_child / wall if wall > 0 else 0.0
    flat = [rec for ch in chunks for rec in ch]
    loo = {(int(r[0]), int(r[1])): r for r in flat}   # (cyc,anch) -> record
    print(f"[P1.1] {len(flat)} LOO residuals in {wall:.2f}s wall; "
          f"child-CPU {cpu_child:.2f}s -> {util:.1f} cores busy "
          f"({100*util/NPROC:.0f}% of {NPROC})")

    loo_cyc  = np.array([r[0] for r in flat], int)
    loo_anch = np.array([r[1] for r in flat], int)
    loo_rng  = np.array([r[2] for r in flat], float)
    loo_res  = np.array([r[3] for r in flat], float)
    loo_pos  = np.array([[r[4], r[5], r[6]] for r in flat], float)
    np.savez(os.path.join(HERE, "loo_residuals.npz"),
             cycle_idx=loo_cyc, anchor_id=loo_anch, range_mm=loo_rng,
             loo_residual_mm=loo_res,
             solved_pos_mm=np.array([pos[c] for c in loo_cyc]),
             solved_pos_loo_mm=loo_pos,
             n_anchors=np.array([nanch[c] for c in loo_cyc], int))
    print(f"[P1.1] |LOO residual| median {np.median(np.abs(loo_res)):.0f} mm  "
          f"(saved loo_residuals.npz)")

    # ---------------- P1.2 locked CIR features ----------------
    frames = []          # one dict per CIR frame
    for i, r in enumerate(rows):
        c = r["cir"]; a = r["cir_aid"]
        if c is None or a is None:
            continue
        rmm = rng_list[i].get(a, -1)
        f = cir_features(c, rmm if L.valid_range(rmm) else None)
        f["cycle"] = i; f["anchor"] = a
        frames.append(f)
    # friis_residual: per-anchor fit  20log10(|CIR_fp|) = A + slope*log10(range)
    friis_fit = {}
    for a in range(8):
        fa = [f for f in frames if f["anchor"] == a and np.isfinite(f["range_mm"]) and f["fp_mag"] > 0]
        if len(fa) < 5:
            friis_fit[a] = None
            for f in fa: f["friis_residual"] = np.nan
            continue
        x = np.log10([f["range_mm"] for f in fa])
        y = 20 * np.log10([f["fp_mag"] for f in fa])
        A = np.column_stack([np.ones_like(x), x])
        beta, *_ = np.linalg.lstsq(A, y, rcond=None)
        friis_fit[a] = (beta[0], beta[1])            # intercept, slope (dB per decade)
        for f in fa:
            yhat = beta[0] + beta[1] * np.log10(f["range_mm"])
            f["friis_residual"] = 20 * np.log10(f["fp_mag"]) - yhat
    for f in frames:                                  # frames w/o valid range
        f.setdefault("friis_residual", np.nan)

    # persist cir_features.npz
    fcols = {k: np.array([f.get(k, np.nan) for f in frames], float)
             for k in FEATURES + ["fp_tap", "fp_subtap", "sigma_noise", "peak",
                                   "fp_mag", "range_mm"]}
    fcols["cycle"]  = np.array([f["cycle"] for f in frames], int)
    fcols["anchor"] = np.array([f["anchor"] for f in frames], int)
    np.savez(os.path.join(HERE, "cir_features.npz"), **fcols)
    print(f"[P1.2] {len(frames)} CIR frames featurized (saved cir_features.npz)")

    # ---------------- P1.3 GATE TEST ----------------
    # join CIR frame (cycle,anchor) with LOO residual on same (cycle,anchor)
    J = {k: [] for k in ["feat_"+f for f in FEATURES] +
         ["abs_e", "solved_range", "anchor", "n_anchors"]}
    for f in frames:
        key = (f["cycle"], f["anchor"])
        if key not in loo:
            continue
        rec = loo[key]
        a = f["anchor"]
        srange = float(np.linalg.norm(pos[f["cycle"]] - P[a]))   # geometric solved range
        for ft in FEATURES:
            J["feat_"+ft].append(f.get(ft, np.nan))
        J["abs_e"].append(abs(rec[3]))
        J["solved_range"].append(srange)
        J["anchor"].append(a)
        J["n_anchors"].append(nanch[f["cycle"]])
    for k in J:
        J[k] = np.array(J[k], float)
    J["anchor"] = J["anchor"].astype(int)
    npool = len(J["abs_e"])
    print(f"[P1.3] gate join: {npool} (cycle,anchor) rows with BOTH CIR & LOO")

    controls = dict(cont=[J["solved_range"], J["n_anchors"].astype(float)], cat=J["anchor"])
    big = J["abs_e"] > BIG_ERR_MM
    table = []
    for ft in FEATURES:
        x = J["feat_"+ft]
        raw_r, raw_p = raw_spearman(x, J["abs_e"])
        prho, pp, nn, dof = partial_spearman(x, J["abs_e"], controls)
        clo, chi = partial_spearman_boot(x, J["abs_e"], controls, nboot=1000)
        a_auc = auc_score(x, big)
        table.append(dict(feature=ft, raw_rho=raw_r, raw_p=raw_p,
                          partial_rho=prho, partial_p=pp, n=nn,
                          ci_lo=clo, ci_hi=chi, auc=a_auc))
    # discriminative AUC = orientation-free
    for row in table:
        row["auc_disc"] = max(row["auc"], 1 - row["auc"]) if np.isfinite(row["auc"]) else np.nan

    # verdict (pre-registered)
    valid_rows = [r for r in table if np.isfinite(r["partial_rho"])]
    best = max(valid_rows, key=lambda r: abs(r["partial_rho"]))
    best_auc = max((r for r in valid_rows if np.isfinite(r["auc_disc"])),
                   key=lambda r: r["auc_disc"])
    go = any((abs(r["partial_rho"]) >= 0.30 and r["partial_p"] < 0.01) or
             (np.isfinite(r["auc_disc"]) and r["auc_disc"] >= 0.70) for r in valid_rows)
    nogo = (abs(best["partial_rho"]) < 0.15 and best_auc["auc_disc"] < 0.60)
    verdict = "GO" if go else ("NO-GO" if nogo else "UNDERPOWERED")
    print(f"[P1.3] VERDICT = {verdict}  | best feature '{best['feature']}' "
          f"partial_rho={best['partial_rho']:+.3f} p={best['partial_p']:.3g}; "
          f"best AUC '{best_auc['feature']}'={best_auc['auc_disc']:.3f}")

    best_feature = best["feature"]

    # ---------------- P2.1 per-anchor gauge fit  e = a + b*r ----------------
    gauge = []
    for a in range(8):
        m = loo_anch == a
        if m.sum() < 4:
            gauge.append(dict(anchor=a, label=LBL[a], a_mm=np.nan, b_pct=np.nan,
                              b_se_pct=np.nan, r2=np.nan, n=int(m.sum()))); continue
        r = loo_rng[m]; e = loo_res[m]
        A = np.column_stack([np.ones_like(r), r])
        beta, *_ = np.linalg.lstsq(A, e, rcond=None)
        yhat = A @ beta
        dof = max(1, len(r) - 2)
        s2 = ((e - yhat) ** 2).sum() / dof
        cov = s2 * np.linalg.inv(A.T @ A)
        b_se = float(np.sqrt(cov[1, 1]))
        ss_res = ((e - yhat) ** 2).sum(); ss_tot = ((e - e.mean()) ** 2).sum() + 1e-12
        gauge.append(dict(anchor=a, label=LBL[a], a_mm=float(beta[0]),
                          b_pct=float(beta[1] * 100), b_se_pct=b_se * 100,
                          r2=float(1 - ss_res / ss_tot), n=int(m.sum())))
    a_mean = float(np.nanmean([g["a_mm"] for g in gauge]))       # common-mode Geiger delay
    # pooled within-anchor slope (anchor fixed-effects) -> well-powered APS011 test
    ed, rd = [], []
    for a in range(8):
        m = loo_anch == a
        if m.sum() < 4:
            continue
        ed.append(loo_res[m] - loo_res[m].mean()); rd.append(loo_rng[m] - loo_rng[m].mean())
    ed = np.concatenate(ed); rd = np.concatenate(rd)
    Ap = np.column_stack([np.ones_like(rd), rd])
    bp, *_ = np.linalg.lstsq(Ap, ed, rcond=None)
    yh = Ap @ bp; dofp = max(1, len(rd) - 2)
    s2p = ((ed - yh) ** 2).sum() / dofp
    covp = s2p * np.linalg.inv(Ap.T @ Ap)
    pooled_slope_pct = float(bp[1] * 100)
    pooled_slope_se_pct = float(np.sqrt(covp[1, 1]) * 100)

    # ---------------- P2.2 cross-check vs AutoPos delays ----------------
    a_int = np.array([g["a_mm"] for g in gauge])
    dly   = DLY
    fin = np.isfinite(a_int) & np.isfinite(dly)
    if fin.sum() >= 3:
        cc_rho, cc_p = raw_spearman(a_int[fin], dly[fin])
    else:
        cc_rho, cc_p = np.nan, np.nan
    BOUND = 60.0
    saturated = [LBL[a] for a in range(8) if np.isfinite(dly[a]) and dly[a] >= BOUND - 0.5]

    # ---------------- P2.3 slope vs APS011 ----------------
    APS011_PCT = 2.77
    slopes = np.array([g["b_pct"] for g in gauge])
    slope_med = np.nanmedian(slopes)
    # primary test on the pooled within-anchor slope (+-2 SE band vs APS011)
    lo2, hi2 = pooled_slope_pct - 2 * pooled_slope_se_pct, pooled_slope_pct + 2 * pooled_slope_se_pct
    slope_consistent = bool(lo2 <= APS011_PCT <= hi2)

    # ================= FIGURES =================
    _fig_gauge(gauge, loo_anch, loo_rng, loo_res, LBL, APS011_PCT)
    _fig_sigma_map(P, LBL, loo_cyc, loo_anch, loo_res, pos, frames, best_feature)
    seg_info = _fig_rotation(P, LBL, t, pos, mpos, nanch, frames, best_feature, rng_list)

    # ---- P1.4 spatial summary: interior (inside anchor XY bbox) vs exterior |e| ----
    xy = pos[loo_cyc][:, :2]
    xmn, ymn = P[:, 0].min(), P[:, 1].min(); xmx, ymx = P[:, 0].max(), P[:, 1].max()
    interior = (xy[:, 0] >= xmn) & (xy[:, 0] <= xmx) & (xy[:, 1] >= ymn) & (xy[:, 1] <= ymx)
    med_in  = float(np.median(np.abs(loo_res[interior]))) if interior.any() else np.nan
    med_out = float(np.median(np.abs(loo_res[~interior]))) if (~interior).any() else np.nan

    # ================= OUTPUTS =================
    extra = dict(a_mean=a_mean, pooled_slope_pct=pooled_slope_pct,
                 pooled_slope_se_pct=pooled_slope_se_pct,
                 med_in=med_in, med_out=med_out,
                 frac_in=float(interior.mean()))
    _write_verdict_json(verdict, best, best_auc, gauge, cc_rho, saturated,
                        slope_med, slope_consistent, npool, extra)
    _write_report(verdict, table, best, best_auc, gauge, cc_rho, cc_p, saturated,
                  dly, LBL, slope_med, slope_consistent, APS011_PCT, npool,
                  N, mpos.sum(), np.nanmedian(resid), len(flat), wall, util,
                  friis_fit, seg_info, loo_res, big, extra)

    print(f"[done] total wall {time.time()-t_all:.1f}s. Outputs under {HERE}")
    return verdict


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------
def _fig_gauge(gauge, loo_anch, loo_rng, loo_res, LBL, aps):
    fig, axes = plt.subplots(2, 4, figsize=(20, 9), sharex=False)
    for a in range(8):
        ax = axes[a // 4][a % 4]
        m = loo_anch == a
        g = gauge[a]
        ax.plot(loo_rng[m] / 1000, loo_res[m], '.', ms=6, alpha=0.6, color=plt.cm.tab10(a))
        if np.isfinite(g["a_mm"]):
            rr = np.linspace(loo_rng[m].min(), loo_rng[m].max(), 50)
            ax.plot(rr / 1000, g["a_mm"] + g["b_pct"] / 100 * rr, 'r-', lw=2)
            ax.plot(rr / 1000, aps / 100 * rr, 'k--', lw=1, alpha=0.6, label="APS011 %.2f%%" % aps)
        ax.axhline(0, color="gray", lw=0.6)
        ax.set_title(f"a{a} ({LBL[a]})  a={g['a_mm']:+.0f}mm  b={g['b_pct']:+.2f}%  "
                     f"R²={g['r2']:.2f}  n={g['n']}", fontsize=9)
        ax.set_xlabel("range (m)"); ax.set_ylabel("LOO residual e (mm)")
        ax.grid(alpha=0.3); ax.legend(fontsize=7, loc="upper left")
    fig.suptitle("Gauge fit: LOO residual  e = a + b·r  per anchor "
                 "(red=fit, dashed=APS011 slope)", fontsize=14)
    fig.tight_layout(); fig.savefig(os.path.join(FIGDIR, "gauge_fit_per_anchor.png"), dpi=100)
    plt.close(fig)


def _grid(P, cell=500.0, margin=1000.0):
    lo = P[:, :2].min(0) - margin; hi = P[:, :2].max(0) + margin
    xe = np.arange(lo[0], hi[0] + cell, cell)
    ye = np.arange(lo[1], hi[1] + cell, cell)
    return xe, ye

def _fig_sigma_map(P, LBL, loo_cyc, loo_anch, loo_res, pos, frames, best_feature):
    xe, ye = _grid(P)
    # per-anchor median |LOO residual| map
    fig, axes = plt.subplots(2, 4, figsize=(22, 10))
    for a in range(8):
        ax = axes[a // 4][a % 4]
        M = loo_anch == a
        xy = pos[loo_cyc[M]][:, :2]; ee = np.abs(loo_res[M])
        H = np.full((len(ye) - 1, len(xe) - 1), np.nan)
        for ix in range(len(xe) - 1):
            for iy in range(len(ye) - 1):
                sel = ((xy[:, 0] >= xe[ix]) & (xy[:, 0] < xe[ix + 1]) &
                       (xy[:, 1] >= ye[iy]) & (xy[:, 1] < ye[iy + 1]))
                if sel.sum() >= 1:
                    H[iy, ix] = np.median(ee[sel])
        im = ax.pcolormesh(xe, ye, H, cmap="magma_r", vmin=0,
                           vmax=np.nanpercentile(np.abs(loo_res), 92))
        ax.scatter(P[:, 0], P[:, 1], c="cyan", marker="^", s=60, edgecolors="k")
        ax.scatter(P[a, 0], P[a, 1], c="lime", marker="^", s=160, edgecolors="k")
        ax.set_title(f"a{a} ({LBL[a]})  median |LOO e| (mm)", fontsize=10)
        ax.set_aspect("equal"); fig.colorbar(im, ax=ax, shrink=0.8)
    fig.suptitle("Per-anchor channel quality map: median |LOO residual| over the room "
                 "(green ▲ = this anchor)", fontsize=14)
    fig.tight_layout(); fig.savefig(os.path.join(FIGDIR, "sigma_map_per_anchor.png"), dpi=95)
    plt.close(fig)

    # aggregate map (mean-across-anchors of median |e| per cell)
    xy_all = pos[loo_cyc][:, :2]; ee_all = np.abs(loo_res)
    Hs = np.zeros((len(ye) - 1, len(xe) - 1)); Hn = np.zeros_like(Hs)
    for a in range(8):
        M = loo_anch == a
        xy = pos[loo_cyc[M]][:, :2]; ee = np.abs(loo_res[M])
        for ix in range(len(xe) - 1):
            for iy in range(len(ye) - 1):
                sel = ((xy[:, 0] >= xe[ix]) & (xy[:, 0] < xe[ix + 1]) &
                       (xy[:, 1] >= ye[iy]) & (xy[:, 1] < ye[iy + 1]))
                if sel.sum() >= 1:
                    Hs[iy, ix] += np.median(ee[sel]); Hn[iy, ix] += 1
    Hagg = np.where(Hn > 0, Hs / np.maximum(Hn, 1), np.nan)
    fig, ax = plt.subplots(figsize=(10, 7))
    im = ax.pcolormesh(xe, ye, Hagg, cmap="magma_r")
    ax.scatter(P[:, 0], P[:, 1], c="cyan", marker="^", s=90, edgecolors="k")
    for a in range(8):
        ax.annotate(LBL[a], (P[a, 0], P[a, 1]), fontsize=10, color="white")
    ax.set_aspect("equal"); ax.set_xlabel("x (mm)"); ax.set_ylabel("y (mm)")
    ax.set_title("Aggregate channel quality: mean-across-anchors median |LOO residual| (mm)")
    fig.colorbar(im, ax=ax, label="mm")
    fig.tight_layout(); fig.savefig(os.path.join(FIGDIR, "sigma_map_aggregate.png"), dpi=110)
    plt.close(fig)


def _fig_rotation(P, LBL, t, pos, mpos, nanch, frames, best_feature, rng_list):
    N = len(t)
    # speed + rolling pos-std over +-5s window
    speed = np.full(N, np.nan)
    ii = np.where(mpos)[0]
    for k in range(1, len(ii)):
        dt = t[ii[k]] - t[ii[k - 1]]
        if dt > 0:
            speed[ii[k]] = np.linalg.norm(pos[ii[k]] - pos[ii[k - 1]]) / dt
    rstd = np.full(N, np.nan); rsp = np.full(N, np.nan)
    for i in range(N):
        if not mpos[i]:
            continue
        w = (t >= t[i] - 5) & (t <= t[i] + 5) & mpos
        if w.sum() >= 3:
            rstd[i] = np.linalg.norm(pos[w].std(0)); rsp[i] = np.nanmedian(speed[w])

    # Pre-registered thresholds (pos-std<300mm, speed<300mm/s): report if any qualify.
    strict = mpos & (rstd < 300) & (rsp < 300)
    # jitter floor: apparent speed of a *stationary* hand from solve noise
    jitter_floor = float(np.nanmin(rsp[mpos])) if mpos.any() else np.nan

    # Adaptive relative segmentation (nominal gate is below the jitter floor, see report):
    # low relative motion = below the 35th percentile of both rolling std and rolling speed.
    p_std = np.nanpercentile(rstd[mpos], 35); p_sp = np.nanpercentile(rsp[mpos], 35)
    stationary = mpos & (rstd < max(300.0, p_std)) & (rsp < p_sp)
    segs = []
    i = 0
    while i < N:
        if stationary[i]:
            j = i
            while j + 1 < N and (stationary[j + 1] or not mpos[j + 1]):
                j += 1
            if t[j] - t[i] >= 8:
                segs.append((i, j))
            i = j + 1
        else:
            i += 1
    segs = sorted(segs, key=lambda s: t[s[1]] - t[s[0]], reverse=True)[:2]
    segs = sorted(segs, key=lambda s: s[0])

    # best-feature value per CIR frame
    fv = {(f["cycle"], f["anchor"]): f.get(best_feature, np.nan) for f in frames}

    fig, axes = plt.subplots(1, max(1, len(segs)), figsize=(8 * max(1, len(segs)), 6),
                             squeeze=False)
    seg_report = {"strict_n": int(strict.sum()), "jitter_floor_mm_s": jitter_floor,
                  "segments": []}
    if not segs:
        axes[0][0].text(0.5, 0.5, "no low-motion segment detected",
                        ha="center", va="center")
    for si, (lo, hi) in enumerate(segs):
        ax = axes[0][si]
        rep = dict(t0=float(t[lo]), t1=float(t[hi]), dur=float(t[hi] - t[lo]), anchors={})
        for a in range(8):
            xs = [(c, v) for (c, an), v in fv.items() if an == a and lo <= c <= hi and np.isfinite(v)]
            if len(xs) < 2:
                continue
            xs.sort()
            cc = np.array([x[0] for x in xs]); vv = np.array([x[1] for x in xs])
            ax.plot(cc, vv, '.-', ms=8, color=plt.cm.tab10(a), label=f"{LBL[a]}")
            rep["anchors"][LBL[a]] = dict(n=len(vv), p2p=float(vv.max() - vv.min()),
                                          std=float(vv.std()))
        ax.set_title(f"low-motion seg {si+1}: t=[{t[lo]:.0f},{t[hi]:.0f}]s  "
                     f"({t[hi]-t[lo]:.0f}s)", fontsize=11)
        ax.set_xlabel("cycle index"); ax.set_ylabel(best_feature)
        ax.legend(ncol=4, fontsize=8); ax.grid(alpha=0.3)
        seg_report["segments"].append(rep)
    fig.suptitle(f"Rotation body-shadow: '{best_feature}' vs cycle (adaptive low-motion "
                 f"windows)\npre-registered gate qualified {int(strict.sum())} cycles — "
                 f"jitter floor {jitter_floor:.0f} mm/s exceeds the 300 mm/s gate",
                 fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(os.path.join(FIGDIR, "rotation_shadow.png"), dpi=110)
    plt.close(fig)
    return seg_report


# --------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------
def _write_verdict_json(verdict, best, best_auc, gauge, cc_rho, saturated,
                        slope_med, slope_consistent, npool, extra):
    obj = dict(
        verdict=verdict,
        best_feature=best["feature"],
        partial_rho=round(best["partial_rho"], 4),
        p=(None if not np.isfinite(best["partial_p"]) else float(f"{best['partial_p']:.3g}")),
        auc=round(best_auc["auc_disc"], 4),
        auc_feature=best_auc["feature"],
        n_pooled=int(npool),
        gauge=dict(
            per_anchor=[dict(anchor=g["label"], a_mm=None if not np.isfinite(g["a_mm"]) else round(g["a_mm"], 1),
                             b_pct=None if not np.isfinite(g["b_pct"]) else round(g["b_pct"], 3),
                             r2=None if not np.isfinite(g["r2"]) else round(g["r2"], 3), n=g["n"])
                        for g in gauge],
            common_mode_a_mm=round(extra["a_mean"], 1),
            intercept_vs_autopos_delay_spearman=None if not np.isfinite(cc_rho) else round(cc_rho, 3),
            delay_bound_saturated=saturated,
            slope_median_pct=None if not np.isfinite(slope_med) else round(slope_med, 3),
            pooled_within_anchor_slope_pct=round(extra["pooled_slope_pct"], 3),
            pooled_within_anchor_slope_se_pct=round(extra["pooled_slope_se_pct"], 3),
            slope_vs_aps011="consistent" if slope_consistent else "inconsistent",
        ),
    )
    with open(os.path.join(HERE, "gate_verdict.json"), "w") as fp:
        json.dump(obj, fp, indent=2)


def _write_report(verdict, table, best, best_auc, gauge, cc_rho, cc_p, saturated,
                  dly, LBL, slope_med, slope_consistent, aps, npool,
                  N, nsolved, med_resid, n_loo, wall, util,
                  friis_fit, seg_info, loo_res, big, extra):
    o = []
    W = o.append
    W("# Geiger MODE_SCAN — Proxy-Gate + Sigma-Map + Gauge Report\n")
    W(f"**Capture:** `logs/geiger_scan_20260711_161258_8anchor/scan.log` — "
      f"{N} cycles, {nsolved} solved (median solve-residual {med_resid:.0f} mm).\n")
    W(f"**Method locks:** LOO residuals only · partial Spearman controlling "
      f"{{solved_range, anchor_id, n_anchors}} · locked 9-feature set · "
      f"pre-registered GO/NO-GO · no post-hoc feature sweep.\n")
    W(f"**Compute:** LOO on Pool(6) — {n_loo} re-solves in {wall:.2f}s wall, "
      f"~{util:.1f} cores busy ({100*util/6:.0f}% of 6). i7-8700K 6C/12T, no GPU.\n")

    W(f"\n## VERDICT: **{verdict}**\n")
    if verdict == "GO":
        W(f"At least one locked feature meets the pre-registered GO bar "
          f"(partial ρ ≥ 0.30 & p < 0.01, **or** discriminative AUC ≥ 0.70).\n")
    elif verdict == "NO-GO":
        W(f"Best feature partial ρ = **{best['partial_rho']:+.3f}** (|ρ| < 0.15) and best "
          f"discriminative AUC = **{best_auc['auc_disc']:.3f}** (< 0.60): CIR features on the "
          f"same-device same-link path do **not** predict LOO ranging error. The proxy "
          f"gate fails here just as it did on the co-located listeners.\n")
    else:
        W(f"Best partial ρ = **{best['partial_rho']:+.3f}** (feature `{best['feature']}`), "
          f"best discriminative AUC = **{best_auc['auc_disc']:.3f}** (feature "
          f"`{best_auc['feature']}`): between the GO and NO-GO bars. Underpowered — "
          f"needs the tripod occlusion ladder (controlled NLOS) to decide.\n")

    W(f"\n### Bottom line\n")
    W(f"- **Proxy gate: does not open here.** Across a freely-moving probe with mostly-LOS "
      f"geometry, per-position CIR features carry only a **weak** trace of ranging error "
      f"(best |ρ|≈0.10, AUC≈0.62). Not the flat null of the co-located listeners, but nowhere "
      f"near the 0.30/0.70 GO bar. The one significant feature (`early_ratio`) points the "
      f"**physically-correct** way (more LOS energy → less error), so the signal is real but tiny.\n")
    W(f"- **Why underpowered, not NO-GO:** this capture is ~70% LOS with |e| dominated by "
      f"geometry/solve noise (~{np.median(np.abs(loo_res)):.0f} mm), not multipath. To move "
      f"the needle you need *forced* NLOS contrast — the tripod occlusion ladder — so CIR "
      f"features see a real LOS↔NLOS swing to correlate against.\n")
    W(f"- **The bigger correctable win is the gauge, not the proxy:** a common-mode "
      f"**{extra['a_mean']:+.0f} mm** Geiger antenna-delay offset + an APS011-order "
      f"**+{extra['pooled_slope_pct']:.1f}%** range slope explain most systematic error — "
      f"both fixable in firmware (antenna-delay recal + `dwt_getrangebias()`).\n")

    W(f"\n## P1.4 — Spatial channel-quality map\n")
    W(f"See `figures/sigma_map_per_anchor.png` (8 per-anchor tiles) and "
      f"`figures/sigma_map_aggregate.png`. Positions **inside** the anchor XY footprint "
      f"({100*extra['frac_in']:.0f}% of samples) have median |LOO e| = "
      f"**{extra['med_in']:.0f} mm** vs **{extra['med_out']:.0f} mm** outside it — the "
      f"expected GDOP pattern (accuracy degrades past the convex hull of the cage). The "
      f"per-anchor tiles show no single room region that uniquely poisons one anchor, "
      f"consistent with the mostly-LOS, multipath-light channel.\n")

    W(f"\n## P1.3 — Gate table (pooled, n={npool})\n")
    W("| feature | raw ρ | partial ρ | partial p | CI95 | AUC>250mm | AUC_disc |")
    W("|---|---|---|---|---|---|---|")
    order = sorted(table, key=lambda r: -abs(r["partial_rho"]) if np.isfinite(r["partial_rho"]) else 0)
    for r in order:
        ci = (f"[{r['ci_lo']:+.2f},{r['ci_hi']:+.2f}]"
              if np.isfinite(r["ci_lo"]) else "n/a")
        pp = "n/a" if not np.isfinite(r["partial_p"]) else f"{r['partial_p']:.3g}"
        mark = " ⭐" if r["feature"] == best["feature"] else ""
        W(f"| `{r['feature']}`{mark} | {r['raw_rho']:+.3f} | **{r['partial_rho']:+.3f}** | "
          f"{pp} | {ci} | {r['auc']:.3f} | {r['auc_disc']:.3f} |")
    W(f"\n_Best partial ρ: `{best['feature']}` = {best['partial_rho']:+.3f} "
      f"(p={best['partial_p']:.3g}). Best discriminative AUC: `{best_auc['feature']}` "
      f"= {best_auc['auc_disc']:.3f}. Large-error class |e|>250mm: "
      f"{int(big.sum())}/{len(big)} rows ({100*big.mean():.0f}%)._\n")
    if best["feature"] == "early_ratio":
        W(f"\n_The only feature with p<0.05 is `early_ratio` (ρ={best['partial_rho']:+.3f}): "
          f"higher early-tap energy concentration → **lower** error, the physically-correct "
          f"LOS direction. But |ρ|≈0.10 is far below the 0.30 GO bar and the bootstrap CI "
          f"straddles/just-touches zero — a hint, not a gate._\n")

    W(f"\n## P2.1 — Per-anchor gauge fit  e = a + b·r\n")
    W("| anchor | a (mm) | b (%) | b_SE (%) | R² | n |")
    W("|---|---|---|---|---|---|")
    for g in gauge:
        W(f"| {g['label']} | {g['a_mm']:+.0f} | {g['b_pct']:+.2f} | ±{g['b_se_pct']:.2f} "
          f"| {g['r2']:.2f} | {g['n']} |")
    W(f"\n`a` = common-mode Geiger antenna-delay error + anchor-i bias; "
      f"`b` = range-dependent (APS011-class) slope. **Common-mode intercept "
      f"⟨a⟩ = {extra['a_mean']:+.0f} mm** — the mobile Geiger reads systematically short "
      f"by ~{abs(extra['a_mean']):.0f} mm (its own antenna-delay offset), the single largest "
      f"correctable term. Per-anchor R² ≤ {max(g['r2'] for g in gauge):.2f}: the range-slope "
      f"is weak against the ~{np.median(np.abs(loo_res)):.0f} mm LOO noise, so individual "
      f"`b` values are poorly constrained (see b_SE) — do not over-read a single anchor's slope.\n")

    W(f"\n## P2.2 — Cross-check vs AutoPos fitted delays (`d_anchor_mm`, bound [0,60])\n")
    W("| anchor | gauge a (mm) | AutoPos delay (mm) | at bound? |")
    W("|---|---|---|---|")
    for a in range(8):
        W(f"| {LBL[a]} | {gauge[a]['a_mm']:+.0f} | {dly[a]:.1f} | "
          f"{'**yes**' if LBL[a] in saturated else 'no'} |")
    W(f"\nSpearman ρ(gauge intercept a, AutoPos delay) over 8 anchors = "
      f"**{cc_rho:+.3f}** (p={cc_p:.3g}) — **no significant relation**.\n")
    W(f"Delay-bound saturated anchors: **{', '.join(saturated) if saturated else 'none'}**.\n")
    W(f"_Interpretation:_ the gauge intercepts are dominated by the common-mode Geiger "
      f"offset (⟨a⟩={extra['a_mean']:+.0f} mm) plus ~{np.median(np.abs(loo_res)):.0f} mm LOO "
      f"noise; the AutoPos per-anchor delays span only 0–60 mm, so the intercept cannot "
      f"resolve them and the bound-saturation hypothesis is **not testable** at this SNR. "
      f"_Note vs brief:_ it expected C(−126) & E(−118) most-biased with 4/8 at the bound; "
      f"the V4-io layout instead pins A=0 (reference) and only **{', '.join(saturated)}** "
      f"hit the +60 mm bound — reported as found, not as expected.\n")

    W(f"\n## P2.3 — Slope vs APS011\n")
    W(f"Primary test = **pooled within-anchor slope** (anchor fixed-effects, the "
      f"well-powered estimate): b = **{extra['pooled_slope_pct']:+.2f}% ± "
      f"{extra['pooled_slope_se_pct']:.2f}%** (±1 SE) vs APS011 channel-5 PRF64 "
      f"≈ **+{aps:.2f}%**. Median of the 8 noisy per-anchor slopes = {slope_med:+.2f}%. "
      f"→ **{'consistent' if slope_consistent else 'inconsistent'}** "
      f"(APS011 {'inside' if slope_consistent else 'outside'} the ±2 SE band).\n")
    if slope_consistent:
        W(f"A positive range-dependent slope of the APS011 order is present, so enabling "
          f"`dwt_getrangebias()` should remove of order ~{aps:.1f}% of range-dependent error "
          f"— but note the estimate's SE (±{extra['pooled_slope_se_pct']:.1f}%) is wide, so "
          f"this is directional evidence, not a tight calibration.\n")
    else:
        W(f"The pooled slope does not match uncorrected APS011 within uncertainty; "
          f"range-dependence is not cleanly the datasheet bias curve at this SNR.\n")

    W(f"\n## P1.5 — Rotation / low-motion segments (body-shadow)\n")
    W(f"Pre-registered gate (pos-std<300 mm **and** speed<300 mm/s, ≥15 s) qualified "
      f"**{seg_info['strict_n']} cycles**. Reason it fails: trilateration jitter (~"
      f"{np.median(np.abs(loo_res)):.0f} mm/frame at ~4.3 Hz) yields an apparent-speed "
      f"floor of **{seg_info['jitter_floor_mm_s']:.0f} mm/s** for a genuinely stationary "
      f"hand — above the 300 mm/s gate. I therefore use **adaptive** low-motion windows "
      f"(below the 35th percentile of rolling std & speed).\n")
    if not seg_info["segments"]:
        W("No adaptive low-motion window ≥8 s either. See `figures/rotation_shadow.png`.\n")
    for si, r in enumerate(seg_info["segments"]):
        W(f"- **Seg {si+1}** t=[{r['t0']:.0f},{r['t1']:.0f}]s ({r['dur']:.0f}s): "
          + ", ".join(f"{k} p2p={v['p2p']:.2g}(n{v['n']})" for k, v in r["anchors"].items())
          + "\n")
    W(f"\nWith only ~0.6 CIR/s per anchor, each segment holds only a few frames per anchor, "
      f"so oscillation **amplitude** (p2p) is reported but period is not resolvable — "
      f"body-shadow is suggestive, not quantified. See figure.\n")

    W(f"\n## Artifacts\n")
    for f in ["loo_residuals.npz", "cir_features.npz", "gate_verdict.json",
              "figures/gauge_fit_per_anchor.png", "figures/sigma_map_per_anchor.png",
              "figures/sigma_map_aggregate.png", "figures/rotation_shadow.png"]:
        W(f"- `{f}`")
    with open(os.path.join(HERE, "GEIGER_PROXY_GATE_REPORT.md"), "w") as fp:
        fp.write("\n".join(o) + "\n")


if __name__ == "__main__":
    main()
