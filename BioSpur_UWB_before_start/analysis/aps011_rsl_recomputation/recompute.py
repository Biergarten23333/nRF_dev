#!/usr/bin/env python3
"""Offline APS011 recomputation using a CIR-DERIVED RSL proxy (no new scans).

Motivation
----------
Deployed `dwt_getrangebias()` over-corrects ~3x on DWM1001C.  It indexes the
APS011 range-bias table by RANGE, implicitly assuming the EVK1000 link budget
(TX -41.3 dBm/MHz, 0 dBi) to convert range -> RSL via Friis.  DWM1001C has a
~3 dBi antenna, so the true RSL is higher and the range->RSL map is wrong.

This script bypasses Friis: it estimates a REAL received-signal-level proxy
directly from the CIR accumulator (already logged in every LSCAN line), indexes
the APS011 Table 2 by that measured RSL, and sweeps a single dB offset (the
unknown absolute calibration constant = link-budget constant we don't trust) to
find the operating point that best flattens the ranging gauge.

Everything reuses the LOCKED pg_lib primitives (parse_log, cir_features,
solve_pos, valid_ids, in_room_box, load_geometry) so parsing / FP detection /
trilateration / LOO are byte-identical to the pre-registered pipeline.

Pure offline analysis.  PRE-APS011 baseline scan ONLY (post scans have ranges
already corrupted by the naive over-correction and cannot be used to calibrate).
"""
import os, sys, json, time, argparse
import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PGLIB_DIR = os.path.join(REPO, "logs", "geiger_scan_20260711_161258_8anchor", "analysis")
BASELINE = os.path.join(REPO, "logs", "geiger_scan_20260711_161258_8anchor", "scan.log")
OUTDIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, PGLIB_DIR)
import pg_lib as L   # locked primitives

# --------------------------------------------------------------------------
# APS011 Table 2 (PRF 64 MHz, 500 MHz BW) -- RSL (dBm) -> range bias (cm)
# convention (matches task + DW1000): range_corrected = range_raw - bias_cm*10 (mm)
# --------------------------------------------------------------------------
APS011_RSL_DBM = np.array([-61,-63,-65,-67,-69,-71,-73,-75,-77,-79,-81,-83,-85,-87,-89,-91,-93], float)
APS011_BIAS_CM = np.array([-11.0,-10.5,-10.0,-9.3,-8.2,-6.9,-5.1,-2.7,0.0,2.1,3.5,4.2,4.9,6.2,7.1,7.6,8.1], float)
# ascending-RSL form for np.interp (clamps at the ends)
_ORDER = np.argsort(APS011_RSL_DBM)
RSL_ASC = APS011_RSL_DBM[_ORDER]
BIAS_ASC = APS011_BIAS_CM[_ORDER]

def aps011_bias_cm(rsl_dbm):
    """Interpolated APS011 bias (cm) for a given RSL (dBm). Clamps outside table."""
    return np.interp(rsl_dbm, RSL_ASC, BIAS_ASC)

# --------------------------------------------------------------------------
# Faithful reimplementation of firmware dwt_getrangebias(ch5, range_m, PRF64).
# Range-indexed table range25cm64PRFnb[chanIdx=3] + CM_OFFSET_64M_NB = -17.
# returns correction in METRES; firmware applies actual = reported - bias.
# --------------------------------------------------------------------------
RANGE25CM_CH5_64 = np.array(
    [1,1,1,2,2,3,4,6,7,9,10,12,13,15,16,17,19,21,23,26,30,42,55,65,85,255], int)
CM_OFFSET_64M_NB = -17

def dwt_getrangebias_ch5_64(range_m):
    """Vectorised firmware-exact range bias (metres)."""
    r = np.atleast_1d(np.asarray(range_m, float))
    ri = np.clip((r * 4.0).astype(int), None, 255)      # integer 25cm units, capped
    tbl = RANGE25CM_CH5_64
    out = np.empty_like(r)
    for k, v in enumerate(ri):
        i = 0
        while v > tbl[i]:
            i += 1
        out[k] = (i + CM_OFFSET_64M_NB) * 0.01           # cm -> m
    return out if out.size > 1 else float(out[0])


# --------------------------------------------------------------------------
# CIR -> RSL proxy.  Uses the locked FP detector (pg_lib.cir_features) for the
# first-path tap, then two proxies:
#   A (primary):  fp_power = mag[fp]^2 + mag[fp+1]^2 + mag[fp+2]^2   (mirrors the
#                 DW1000 FP formula F1^2+F2^2+F3^2);  rsl_A = 10*log10(fp_power)
#   B (ref):      rsl_B = 20*log10(fp_mag)
#   S (ref):      fp SNR = 10*log10(fp_power / noise_power)
# All are in an arbitrary dB scale; the sweep supplies the absolute offset.
# --------------------------------------------------------------------------
def cir_rsl_proxies(mag, range_mm):
    f = L.cir_features(mag, range_mm)
    fp = int(f["fp_tap"])
    noise = mag[L.NOISE_LO:L.NOISE_HI]
    npow = float((noise.astype(float) ** 2).mean()) + 1e-9
    fp_power = float(mag[fp] ** 2 + mag[fp + 1] ** 2 + mag[fp + 2] ** 2) + 1e-9
    return dict(
        rsl_A=10.0 * np.log10(fp_power),
        rsl_B=20.0 * np.log10(float(f["fp_mag"]) + 1e-9),
        rsl_S=10.0 * np.log10(fp_power / npow),
        fp_tap=fp, fp_mag=float(f["fp_mag"]),
    )


# --------------------------------------------------------------------------
# pipeline: solve_all -> loo -> gauge (thin wrappers over pg_lib)
# --------------------------------------------------------------------------
def solve_all(P, rng_list):
    N = len(rng_list)
    pos = np.full((N, 3), np.nan)
    x0 = P.mean(0)
    for i, rg in enumerate(rng_list):
        ids = L.valid_ids(rg)
        s, _ids, _rms = L.solve_pos(P, rg, x0, ids=ids)
        if s is not None and L.in_room_box(s):
            pos[i] = s
            x0 = s
    return pos

def loo_residuals(P, rng_list, pos):
    """Signed LOO residuals e = measured - predicted, per (cycle, anchor).
    Only cycles with >=6 valid responders (matches pg_pipeline / comparison)."""
    anch, rng, res = [], [], []
    for i, rg in enumerate(rng_list):
        ids = L.valid_ids(rg)
        if len(ids) < 6:
            continue
        x0 = pos[i] if np.all(np.isfinite(pos[i])) else P.mean(0)
        for a in ids:
            rem = [j for j in ids if j != a]
            s, _ids, _rms = L.solve_pos(P, rg, x0, ids=rem)
            if s is None:
                continue
            e = rg[a] - float(np.linalg.norm(s - P[a]))
            anch.append(a); rng.append(float(rg[a])); res.append(e)
    return dict(anch=np.array(anch, int), rng=np.array(rng, float), res=np.array(res, float))

def gauge_fit(arr):
    """Per-anchor gauge e=a+b*r, common-mode <a>, pooled within-anchor slope."""
    anch, rng, res = arr["anch"], arr["rng"], arr["res"]
    a_list, b_list = [], []
    ed, rd = [], []
    for a in range(8):
        m = anch == a
        if m.sum() < 4:
            a_list.append(np.nan); b_list.append(np.nan); continue
        r = rng[m]; e = res[m]
        A = np.column_stack([np.ones_like(r), r])
        beta, *_ = np.linalg.lstsq(A, e, rcond=None)
        a_list.append(float(beta[0])); b_list.append(float(beta[1] * 100))
        ed.append(e - e.mean()); rd.append(r - r.mean())
    common_mode = float(np.nanmean(a_list))
    if ed:
        ed = np.concatenate(ed); rd = np.concatenate(rd)
        Ap = np.column_stack([np.ones_like(rd), rd])
        bp, *_ = np.linalg.lstsq(Ap, ed, rcond=None)
        pooled_slope = float(bp[1] * 100)
    else:
        pooled_slope = np.nan
    return dict(common_mode_a_mm=common_mode, pooled_slope_pct=pooled_slope,
                slope_median_pct=float(np.nanmedian(b_list)))

def evaluate(P, rng_list):
    pos = solve_all(P, rng_list)
    arr = loo_residuals(P, rng_list, pos)
    g = gauge_fit(arr)
    loo_med = float(np.median(np.abs(arr["res"]))) if len(arr["res"]) else np.nan
    return dict(pooled_slope_pct=g["pooled_slope_pct"], slope_median_pct=g["slope_median_pct"],
                common_mode_mm=g["common_mode_a_mm"], loo_abs_median_mm=loo_med,
                n_loo=int(len(arr["res"])), n_solved=int(np.isfinite(pos[:, 0]).sum()))


# --------------------------------------------------------------------------
# corrections applied to a base rng_list  (each returns a NEW rng_list)
# --------------------------------------------------------------------------
def correct_none(rng_list):
    return rng_list

def correct_naive_firmware(rng_list):
    """Exactly what the deployed firmware does (range-indexed Table 2)."""
    out = []
    for rg in rng_list:
        d = {}
        for a, mm in rg.items():
            if L.valid_range(mm):
                bias_m = dwt_getrangebias_ch5_64(mm / 1000.0)
                d[a] = int(round(mm - bias_m * 1000.0))
            else:
                d[a] = mm
        out.append(d)
    return out

def make_rsl_corrector(rsl_model, center_dbm, model_median_dbm):
    """Return a corrector: RSL-indexed Table 2, shifted so the population median
    RSL maps to `center_dbm`.  rsl_model(anchor, range_mm) -> dB (arbitrary scale)."""
    shift = center_dbm - model_median_dbm
    def corr(rng_list):
        out = []
        for rg in rng_list:
            d = {}
            for a, mm in rg.items():
                if L.valid_range(mm):
                    rsl = rsl_model(a, mm) + shift
                    bias_cm = float(aps011_bias_cm(rsl))
                    d[a] = int(round(mm - bias_cm * 10.0))
                else:
                    d[a] = mm
            out.append(d)
        return out
    return corr

def make_slope_only_corrector(slope_pct, pivot_mm):
    """Reference: remove exactly the baseline pooled slope (no table)."""
    b = slope_pct / 100.0
    def corr(rng_list):
        out = []
        for rg in rng_list:
            d = {a: (int(round(mm - b * (mm - pivot_mm))) if L.valid_range(mm) else mm)
                 for a, mm in rg.items()}
            out.append(d)
        return out
    return corr


# --------------------------------------------------------------------------
# sweep worker (module-level for multiprocessing pickling)
# --------------------------------------------------------------------------
_G = {}
def _sweep_init(P, rng_list, model_params, model_median, which):
    _G["P"] = P; _G["rng"] = rng_list
    _G["mp"] = model_params; _G["mm"] = model_median; _G["which"] = which

def _rsl_model_from_params(params, which):
    alpha = params["alpha"]; beta = params["beta"]
    pa, pb = params["pool_alpha"], params["pool_beta"]
    def model(a, r_mm):
        al = alpha.get(a); be = beta.get(a)
        if al is None or not np.isfinite(al):
            al, be = pa, pb
        return al + be * np.log10(max(r_mm, 1.0))
    return model

def _sweep_one(center):
    model = _rsl_model_from_params(_G["mp"], _G["which"])
    corr = make_rsl_corrector(model, center, _G["mm"])
    m = evaluate(_G["P"], corr(_G["rng"]))
    m["center_dbm"] = float(center)
    return m


# --------------------------------------------------------------------------
def fit_rsl_model(points):
    """points: list of (anchor, range_mm, rsl_dB). Per-anchor rsl = a + b*log10(r).
    Returns params dict + diagnostics."""
    per_alpha, per_beta, per_n, per_r = {}, {}, {}, {}
    allr, allrsl = [], []
    for a in range(8):
        pa = [(r, s) for (an, r, s) in points if an == a and r > 0 and np.isfinite(s)]
        if len(pa) < 8:
            continue
        r = np.array([p[0] for p in pa], float); s = np.array([p[1] for p in pa], float)
        x = np.log10(r)
        A = np.column_stack([np.ones_like(x), x])
        beta, *_ = np.linalg.lstsq(A, s, rcond=None)
        per_alpha[a] = float(beta[0]); per_beta[a] = float(beta[1]); per_n[a] = len(pa)
        pr = np.corrcoef(x, s)[0, 1]
        per_r[a] = float(pr)
        allr.append(r); allrsl.append(s)
    # pooled fit (fallback + diagnostic)
    R = np.concatenate(allr); S = np.concatenate(allrsl)
    X = np.log10(R)
    Ap = np.column_stack([np.ones_like(X), X])
    bp, *_ = np.linalg.lstsq(Ap, S, rcond=None)
    pool_alpha, pool_beta = float(bp[0]), float(bp[1])
    pearson_logr = float(np.corrcoef(X, S)[0, 1])
    pearson_r = float(np.corrcoef(R, S)[0, 1])
    return dict(alpha=per_alpha, beta=per_beta, pool_alpha=pool_alpha, pool_beta=pool_beta,
                per_n=per_n, per_r=per_r, pearson_logr=pearson_logr, pearson_r=pearson_r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", default=BASELINE)
    ap.add_argument("--which", default="A", choices=["A", "B", "S"],
                    help="RSL proxy: A=fp_power(3-tap), B=20log10(fp_mag), S=fp SNR")
    ap.add_argument("--nproc", type=int, default=10)
    ap.add_argument("--sweep-lo", type=float, default=-95.0)
    ap.add_argument("--sweep-hi", type=float, default=-55.0)
    ap.add_argument("--sweep-step", type=float, default=0.5)
    args = ap.parse_args()

    t0 = time.time()
    ncpu = os.cpu_count()
    P, LBL, DLY, W, Wc = L.load_geometry()
    rows = L.parse_log(args.scan)
    rng_list = [r["rng"] for r in rows]
    print(f"[data] {args.scan}")
    print(f"[data] {len(rows)} LSCAN cycles; "
          f"{sum(r['cir'] is not None for r in rows)} with CIR")

    # ---- build RSL proxy points (cir_aid anchor only) --------------------
    key = {"A": "rsl_A", "B": "rsl_B", "S": "rsl_S"}[args.which]
    points = []          # (anchor, range_mm, rsl_dB) for chosen proxy
    all_proxies = []     # keep A/B/S for cross-diagnostics
    for i, r in enumerate(rows):
        c = r["cir"]; a = r["cir_aid"]
        if c is None or a is None:
            continue
        mm = rng_list[i].get(a, -1)
        if not L.valid_range(mm):
            continue
        pr = cir_rsl_proxies(c, mm)
        points.append((a, float(mm), pr[key]))
        all_proxies.append((a, float(mm), pr["rsl_A"], pr["rsl_B"], pr["rsl_S"]))
    print(f"[rsl ] {len(points)} CIR->RSL points (proxy {args.which})")

    mp = fit_rsl_model(points)
    model = _rsl_model_from_params(mp, args.which)

    # population-median model RSL over ALL (cycle, valid anchor) ranges
    model_vals = []
    for rg in rng_list:
        for a, mm in rg.items():
            if L.valid_range(mm):
                model_vals.append(model(a, mm))
    model_vals = np.array(model_vals, float)
    model_median = float(np.median(model_vals))

    # cross-proxy Pearson (which proxy tracks range best?)
    AP = np.array(all_proxies, float)
    def pear(col):
        lr = np.log10(AP[:, 1])
        return float(np.corrcoef(lr, AP[:, col])[0, 1])
    proxy_diag = {"A_vs_logr": pear(2), "B_vs_logr": pear(3), "S_vs_logr": pear(4)}

    print(f"[fit ] pooled RSL = {mp['pool_alpha']:.2f} + {mp['pool_beta']:.2f}*log10(r_mm)")
    print(f"[fit ] Pearson(rsl,log10 r) = {mp['pearson_logr']:+.3f} ; "
          f"per-anchor beta dB/decade: " +
          ", ".join(f"{LBL[a]}:{mp['beta'].get(a, float('nan')):+.1f}" for a in range(8)))
    print(f"[fit ] proxy Pearson vs log-range: A={proxy_diag['A_vs_logr']:+.3f} "
          f"B={proxy_diag['B_vs_logr']:+.3f} S={proxy_diag['S_vs_logr']:+.3f}")
    print(f"[fit ] model median RSL(pop) = {model_median:.2f} dB (arb scale)")

    # ---- reference corrections ------------------------------------------
    base = evaluate(P, rng_list)
    naive = evaluate(P, correct_naive_firmware(rng_list))
    slope_only = evaluate(P, make_slope_only_corrector(
        base["pooled_slope_pct"], float(np.median([mm for rg in rng_list for mm in rg.values()
                                                   if L.valid_range(mm)])))(rng_list))
    print(f"\n[ref ] raw          slope={base['pooled_slope_pct']:+.2f}% "
          f"cm={base['common_mode_mm']:+.0f} LOO={base['loo_abs_median_mm']:.0f} n={base['n_loo']}")
    print(f"[ref ] naive fw     slope={naive['pooled_slope_pct']:+.2f}% "
          f"cm={naive['common_mode_mm']:+.0f} LOO={naive['loo_abs_median_mm']:.0f}")
    print(f"[ref ] slope-only   slope={slope_only['pooled_slope_pct']:+.2f}% "
          f"cm={slope_only['common_mode_mm']:+.0f} LOO={slope_only['loo_abs_median_mm']:.0f}")

    # ---- sweep the RSL operating-center ---------------------------------
    centers = np.arange(args.sweep_lo, args.sweep_hi + 1e-9, args.sweep_step)
    tw = time.time(); to = os.times()
    from multiprocessing import Pool
    with Pool(args.nproc, initializer=_sweep_init,
              initargs=(P, rng_list, mp, model_median, args.which)) as pool:
        sweep = pool.map(_sweep_one, list(centers), chunksize=2)
    wall = time.time() - tw; ot = os.times()
    child_cpu = ((ot.children_user + ot.children_system)
                 - (to.children_user + to.children_system))

    sweep = sorted(sweep, key=lambda d: d["center_dbm"])
    # find optima
    def argbest(keyfn):
        cand = [s for s in sweep if np.isfinite(keyfn(s))]
        return min(cand, key=keyfn) if cand else None
    best_slope = argbest(lambda s: abs(s["pooled_slope_pct"]))
    best_cm = argbest(lambda s: abs(s["common_mode_mm"]))
    best_loo = argbest(lambda s: s["loo_abs_median_mm"])

    # RSL operating span at the LOO optimum
    def span_at(center):
        shift = center - model_median
        v = model_vals + shift
        return float(np.percentile(v, 5)), float(np.median(v)), float(np.percentile(v, 95))

    result = dict(
        scan=args.scan, proxy=args.which, n_points=len(points),
        rsl_model=dict(pool_alpha=mp["pool_alpha"], pool_beta=mp["pool_beta"],
                       pearson_logr=mp["pearson_logr"], pearson_r=mp["pearson_r"],
                       per_anchor_beta={LBL[a]: mp["beta"].get(a) for a in range(8)},
                       per_anchor_n={LBL[a]: mp["per_n"].get(a) for a in range(8)}),
        proxy_pearson_logr=proxy_diag, model_median_rsl=model_median,
        refs=dict(raw=base, naive_firmware=naive, slope_only=slope_only),
        sweep=sweep,
        optima=dict(
            min_abs_slope=best_slope, min_abs_common_mode=best_cm, min_loo=best_loo,
        ),
        span_at_loo_opt=(span_at(best_loo["center_dbm"]) if best_loo else None),
        span_at_slope_opt=(span_at(best_slope["center_dbm"]) if best_slope else None),
        compute=dict(ncpu=ncpu, nproc=args.nproc, sweep_wall_s=wall,
                     sweep_child_cpu_s=child_cpu,
                     cores_busy=(child_cpu / wall if wall > 0 else 0.0),
                     n_centers=len(centers)),
    )
    outjson = os.path.join(OUTDIR, "recompute_result.json")
    with open(outjson, "w") as fp:
        json.dump(result, fp, indent=2, default=float)

    # ---- report ---------------------------------------------------------
    print("\n" + "=" * 74)
    print("SWEEP OPTIMA (RSL-indexed APS011 Table 2, CIR proxy %s)" % args.which)
    print("=" * 74)
    for name, s in [("min |slope|", best_slope), ("min |common-mode|", best_cm),
                    ("min LOO median", best_loo)]:
        if s:
            print(f"  {name:>18}: center={s['center_dbm']:+.1f} dBm  "
                  f"slope={s['pooled_slope_pct']:+.2f}%  cm={s['common_mode_mm']:+.0f}mm  "
                  f"LOO={s['loo_abs_median_mm']:.0f}mm")
    if best_loo:
        lo, md, hi = span_at(best_loo["center_dbm"])
        print(f"  RSL operating span @LOO-opt: p5={lo:.1f}  med={md:.1f}  p95={hi:.1f} dBm")

    print("\n## Three/four-method comparison (same baseline data)")
    print("| method                       |  slope % | common-mode mm | LOO median mm |")
    print("|------------------------------|----------|----------------|---------------|")
    def row(nm, m):
        print(f"| {nm:<28} | {m['pooled_slope_pct']:+7.2f}  | "
              f"{m['common_mode_mm']:+13.0f}  | {m['loo_abs_median_mm']:12.0f}  |")
    row("raw (no correction)", base)
    row("naive dwt_getrangebias", naive)
    if best_loo: row("CIR-RSL Table2 (min LOO)", best_loo)
    if best_slope: row("CIR-RSL Table2 (slope=0)", best_slope)
    row("slope-only (reference)", slope_only)

    print(f"\n## Compute: {ncpu} logical CPUs; Pool({args.nproc}); "
          f"{len(centers)} sweep points; sweep child-CPU {child_cpu:.1f}s / "
          f"{wall:.1f}s wall -> {child_cpu/wall:.1f} cores busy.")
    print(f"[done] {time.time()-t0:.1f}s total. Wrote {outjson}")

    # optional sweep plot
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        c = np.array([s["center_dbm"] for s in sweep])
        sl = np.array([s["pooled_slope_pct"] for s in sweep])
        cm = np.array([s["common_mode_mm"] for s in sweep])
        lo = np.array([s["loo_abs_median_mm"] for s in sweep])
        fig, ax = plt.subplots(3, 1, figsize=(7, 8), sharex=True)
        ax[0].plot(c, sl); ax[0].axhline(0, color="k", lw=.5)
        ax[0].axhline(base["pooled_slope_pct"], color="r", ls="--", lw=.7, label="raw")
        ax[0].set_ylabel("pooled slope %"); ax[0].legend()
        ax[1].plot(c, cm); ax[1].axhline(0, color="k", lw=.5)
        ax[1].set_ylabel("common-mode mm")
        ax[2].plot(c, lo); ax[2].axhline(base["loo_abs_median_mm"], color="r", ls="--", lw=.7, label="raw")
        ax[2].axhline(naive["loo_abs_median_mm"], color="orange", ls="--", lw=.7, label="naive")
        ax[2].set_ylabel("LOO |resid| median mm"); ax[2].set_xlabel("RSL operating center (dBm)")
        ax[2].legend()
        fig.suptitle(f"CIR-RSL APS011 sweep (proxy {args.which})")
        fig.tight_layout()
        figp = os.path.join(OUTDIR, "sweep.png")
        fig.savefig(figp, dpi=110); print(f"[plot] {figp}")
    except Exception as e:
        print(f"[plot] skipped ({e})")


if __name__ == "__main__":
    main()
