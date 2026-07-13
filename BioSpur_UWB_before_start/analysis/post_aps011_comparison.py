#!/usr/bin/env python3
"""Pre vs post APS011 Geiger MODE_SCAN comparison + proxy-gate re-run.

Reuses the LOCKED primitives in `pg_lib.py` (the same module `pg_pipeline.py`
uses) so the parsing, trilateration, LOO residuals, CIR features, gauge fit and
GO/NO-GO thresholds are byte-for-byte identical to the pre-registered pipeline.
Standalone: only numpy + scipy (via pg_lib). No notebook, no matplotlib.

What it does, for BOTH the pre- and post-APS011 scan logs:
  * parse LSCAN lines, trilaterate the probe per cycle (warm-started LM)
  * leave-one-out (LOO) residuals per (cycle, anchor)  -> honest error label
  * per-anchor mean measured range
  * gauge fit  e = a + b*r   (e = LOO residual, r = measured range), per anchor,
    common-mode intercept <a>, and the pooled within-anchor slope (anchor
    fixed-effects) -- the well-powered APS011 slope estimate
Then, for the POST scan, the full proxy gate:
  * LOO |residual| median
  * locked CIR feature set, partial Spearman(feature, |e| | solved_range,
    n_anchors, anchor), best partial rho
  * AUC for the large-error class |e| > 250 mm (orientation-free auc_disc)
  * pre-registered verdict GO / NO-GO / UNDERPOWERED

Usage:
  python3 analysis/post_aps011_comparison.py PRE_SCAN.log POST_SCAN.log \
      [--pglib DIR] [--nproc N] [--out analysis/post_aps011_comparison.json]

  # self-check (must reproduce logs/.../analysis/gate_verdict.json):
  python3 analysis/post_aps011_comparison.py PRE.log PRE.log --check-verdict gate_verdict.json
"""
import os, sys, json, time, argparse
import numpy as np

# ---- pre-registered constants (identical to pg_pipeline.py) ----------------
BIG_ERR_MM = 250.0            # large-error class threshold for AUC
APS011_PCT = 2.77             # APS011 channel-5 / PRF64 datasheet slope (~ +2.77%)
GO_RHO, GO_P, GO_AUC = 0.30, 0.01, 0.70
NOGO_RHO, NOGO_AUC = 0.15, 0.60

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_PGLIB = os.path.join(
    REPO, "logs", "geiger_scan_20260711_161258_8anchor", "analysis")

# ---------------------------------------------------------------------------
# LOO worker (module-level so multiprocessing can pickle it; globals set per
# Pool via the initializer, exactly like pg_pipeline._loo_cycle)
# ---------------------------------------------------------------------------
_P = _RNG = _POS = None
def _loo_init(P, rng_list, pos_full):
    global _P, _RNG, _POS
    _P, _RNG, _POS = P, rng_list, pos_full

def _loo_cycle(i):
    rg = _RNG[i]
    ids = _valid_ids(rg)
    if len(ids) < 6:                                   # LOO only when >=6 responders
        return []
    x0 = _POS[i] if np.all(np.isfinite(_POS[i])) else _P.mean(0)
    out = []
    for a in ids:
        rem = [j for j in ids if j != a]               # >=5 remaining
        s, _ids, _rms = _solve_pos(_P, rg, x0, ids=rem)
        if s is None:
            continue
        e = rg[a] - float(np.linalg.norm(s - _P[a]))   # signed LOO residual (measured - predicted)
        out.append((i, a, float(rg[a]), e, s[0], s[1], s[2]))
    return out


# ---------------------------------------------------------------------------
# pipeline stages (faithful transcription of pg_pipeline.main using pg_lib)
# ---------------------------------------------------------------------------
def solve_all(P, rng_list):
    """P1.0 sequential warm-started full solve. Returns pos[N,3], resid[N], nanch[N]."""
    N = len(rng_list)
    pos = np.full((N, 3), np.nan)
    resid = np.full(N, np.nan)
    nanch = np.zeros(N, int)
    x0 = P.mean(0)
    for i, rg in enumerate(rng_list):
        ids = _valid_ids(rg)
        nanch[i] = len(ids)
        s, _ids, rms = _solve_pos(P, rg, x0, ids=ids)
        if s is not None and _in_room_box(s):
            pos[i] = s; resid[i] = rms; x0 = s
    return pos, resid, nanch


def loo_residuals(P, rng_list, pos, nproc):
    """P1.1 LOO residuals via Pool(nproc). Returns dict + arrays + (child_cpu_s, wall_s)."""
    from multiprocessing import Pool
    cand = [i for i in range(len(rng_list)) if len(_valid_ids(rng_list[i])) >= 6]
    tw = time.time(); to = os.times()
    with Pool(nproc, initializer=_loo_init, initargs=(P, rng_list, pos)) as pool:
        chunks = pool.map(_loo_cycle, cand, chunksize=8)
    wall = time.time() - tw; ot = os.times()
    child_cpu = ((ot.children_user + ot.children_system)
                 - (to.children_user + to.children_system))
    flat = [rec for ch in chunks for rec in ch]
    loo = {(int(r[0]), int(r[1])): r for r in flat}
    arr = dict(
        cyc=np.array([r[0] for r in flat], int),
        anch=np.array([r[1] for r in flat], int),
        rng=np.array([r[2] for r in flat], float),
        res=np.array([r[3] for r in flat], float),
    )
    return loo, arr, (child_cpu, wall, len(flat))


def cir_frames(P, rows, rng_list):
    """P1.2 locked CIR features per (cycle, anchor) + per-anchor friis_residual."""
    frames = []
    for i, r in enumerate(rows):
        c = r["cir"]; a = r["cir_aid"]
        if c is None or a is None:
            continue
        rmm = rng_list[i].get(a, -1)
        f = _cir_features(c, rmm if _valid_range(rmm) else None)
        f["cycle"] = i; f["anchor"] = a
        frames.append(f)
    for a in range(8):
        fa = [f for f in frames if f["anchor"] == a
              and np.isfinite(f["range_mm"]) and f["fp_mag"] > 0]
        if len(fa) < 5:
            for f in fa: f["friis_residual"] = np.nan
            continue
        x = np.log10([f["range_mm"] for f in fa])
        y = 20 * np.log10([f["fp_mag"] for f in fa])
        A = np.column_stack([np.ones_like(x), x])
        beta, *_ = np.linalg.lstsq(A, y, rcond=None)
        for f in fa:
            yhat = beta[0] + beta[1] * np.log10(f["range_mm"])
            f["friis_residual"] = 20 * np.log10(f["fp_mag"]) - yhat
    for f in frames:
        f.setdefault("friis_residual", np.nan)
    return frames


def gate_test(P, frames, loo, pos, nanch):
    """P1.3 gate join + partial Spearman + AUC + pre-registered verdict."""
    J = {k: [] for k in ["feat_" + f for f in _FEATURES]
         + ["abs_e", "solved_range", "anchor", "n_anchors"]}
    for f in frames:
        key = (f["cycle"], f["anchor"])
        if key not in loo:
            continue
        rec = loo[key]; a = f["anchor"]
        srange = float(np.linalg.norm(pos[f["cycle"]] - P[a]))
        for ft in _FEATURES:
            J["feat_" + ft].append(f.get(ft, np.nan))
        J["abs_e"].append(abs(rec[3]))
        J["solved_range"].append(srange)
        J["anchor"].append(a)
        J["n_anchors"].append(nanch[f["cycle"]])
    for k in J:
        J[k] = np.array(J[k], float)
    J["anchor"] = J["anchor"].astype(int)
    npool = len(J["abs_e"])

    controls = dict(cont=[J["solved_range"], J["n_anchors"].astype(float)], cat=J["anchor"])
    big = J["abs_e"] > BIG_ERR_MM
    table = []
    for ft in _FEATURES:
        x = J["feat_" + ft]
        raw_r, raw_p = _raw_spearman(x, J["abs_e"])
        prho, pp, nn, dof = _partial_spearman(x, J["abs_e"], controls)
        clo, chi = _partial_spearman_boot(x, J["abs_e"], controls, nboot=1000)
        a_auc = _auc_score(x, big)
        auc_disc = max(a_auc, 1 - a_auc) if np.isfinite(a_auc) else np.nan
        table.append(dict(feature=ft, raw_rho=raw_r, raw_p=raw_p, partial_rho=prho,
                          partial_p=pp, n=nn, ci_lo=clo, ci_hi=chi,
                          auc=a_auc, auc_disc=auc_disc))
    valid = [r for r in table if np.isfinite(r["partial_rho"])]
    valid_auc = [r for r in valid if np.isfinite(r["auc_disc"])]
    if not valid or not valid_auc:
        # too few joined (cycle,anchor) rows to compute the gate (partial Spearman
        # needs n>=10). Degrade instead of crashing; the real post scan (2-3 min
        # walk, same geometry) will have hundreds of rows.
        return dict(table=table, best=None, best_auc=None,
                    verdict="INSUFFICIENT-DATA", npool=npool, n_big=int(big.sum()))
    best = max(valid, key=lambda r: abs(r["partial_rho"]))
    best_auc = max(valid_auc, key=lambda r: r["auc_disc"])
    go = any((abs(r["partial_rho"]) >= GO_RHO and r["partial_p"] < GO_P)
             or (np.isfinite(r["auc_disc"]) and r["auc_disc"] >= GO_AUC) for r in valid)
    nogo = (abs(best["partial_rho"]) < NOGO_RHO and best_auc["auc_disc"] < NOGO_AUC)
    verdict = "GO" if go else ("NO-GO" if nogo else "UNDERPOWERED")
    return dict(table=table, best=best, best_auc=best_auc, verdict=verdict,
                npool=npool, n_big=int(big.sum()))


def gauge_fit(arr):
    """P2.1 per-anchor gauge e=a+b*r + common-mode <a> + pooled within-anchor slope."""
    anch, rng, res = arr["anch"], arr["rng"], arr["res"]
    per = []
    for a in range(8):
        m = anch == a
        if m.sum() < 4:
            per.append(dict(anchor=a, a_mm=np.nan, b_pct=np.nan, b_se_pct=np.nan,
                            r2=np.nan, n=int(m.sum()))); continue
        r = rng[m]; e = res[m]
        A = np.column_stack([np.ones_like(r), r])
        beta, *_ = np.linalg.lstsq(A, e, rcond=None)
        yhat = A @ beta
        dof = max(1, len(r) - 2)
        s2 = ((e - yhat) ** 2).sum() / dof
        cov = s2 * np.linalg.inv(A.T @ A)
        ss_res = ((e - yhat) ** 2).sum(); ss_tot = ((e - e.mean()) ** 2).sum() + 1e-12
        per.append(dict(anchor=a, a_mm=float(beta[0]), b_pct=float(beta[1] * 100),
                        b_se_pct=float(np.sqrt(cov[1, 1]) * 100),
                        r2=float(1 - ss_res / ss_tot), n=int(m.sum())))
    a_mean = float(np.nanmean([g["a_mm"] for g in per]))
    ed, rd = [], []
    for a in range(8):
        m = anch == a
        if m.sum() < 4:
            continue
        ed.append(res[m] - res[m].mean()); rd.append(rng[m] - rng[m].mean())
    ed = np.concatenate(ed); rd = np.concatenate(rd)
    Ap = np.column_stack([np.ones_like(rd), rd])
    bp, *_ = np.linalg.lstsq(Ap, ed, rcond=None)
    yh = Ap @ bp; dofp = max(1, len(rd) - 2)
    s2p = ((ed - yh) ** 2).sum() / dofp
    covp = s2p * np.linalg.inv(Ap.T @ Ap)
    return dict(per_anchor=per, common_mode_a_mm=a_mean,
                pooled_slope_pct=float(bp[1] * 100),
                pooled_slope_se_pct=float(np.sqrt(covp[1, 1]) * 100),
                slope_median_pct=float(np.nanmedian([g["b_pct"] for g in per])))


def per_anchor_mean_range(rng_list):
    """Mean of valid measured ranges per anchor (mm)."""
    acc = {a: [] for a in range(8)}
    for rg in rng_list:
        for a, mm in rg.items():
            if _valid_range(mm):
                acc[a].append(mm)
    return {a: (float(np.mean(v)) if v else np.nan) for a, v in acc.items()}


def run_pipeline(path, P, nproc):
    rows = _parse_log(path)
    rng_list = [r["rng"] for r in rows]
    n_cir = sum(r["cir"] is not None for r in rows)
    pos, resid, nanch = solve_all(P, rng_list)
    loo, arr, (cpu, wall, n_loo) = loo_residuals(P, rng_list, pos, nproc)
    frames = cir_frames(P, rows, rng_list)
    gate = gate_test(P, frames, loo, pos, nanch)
    gauge = gauge_fit(arr)
    return dict(
        path=path, n_cycles=len(rows), n_cir=n_cir,
        n_solved=int(np.isfinite(pos[:, 0]).sum()),
        median_solve_resid=float(np.nanmedian(resid)),
        per_anchor_mean=per_anchor_mean_range(rng_list),
        loo_abs_median=float(np.median(np.abs(arr["res"]))) if len(arr["res"]) else np.nan,
        n_loo=n_loo, gate=gate, gauge=gauge,
        cpu=dict(child_cpu_s=cpu, wall_s=wall, cores_busy=(cpu / wall if wall > 0 else 0.0)),
    )


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def _fmt(v, sign=False, nd=1):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    return (f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}")

def print_report(pre, post, LBL, nproc):
    def gmean(pa):  # grand mean of per-anchor means
        vs = [v for v in pa.values() if np.isfinite(v)]
        return float(np.mean(vs)) if vs else np.nan

    print("\n" + "=" * 78)
    print("APS011 PRE vs POST — Geiger MODE_SCAN comparison")
    print("=" * 78)
    print(f"pre : {pre['path']}")
    print(f"      {pre['n_cycles']} cycles, {pre['n_cir']} CIRs, "
          f"{pre['n_solved']} solved, {pre['n_loo']} LOO residuals")
    print(f"post: {post['path']}")
    print(f"      {post['n_cycles']} cycles, {post['n_cir']} CIRs, "
          f"{post['n_solved']} solved, {post['n_loo']} LOO residuals")

    pre_gm, post_gm = gmean(pre["per_anchor_mean"]), gmean(post["per_anchor_mean"])
    print("\n## Comparison table")
    print("| metric              | before APS011 | after APS011 | delta    |")
    print("|---------------------|---------------|--------------|----------|")
    print(f"| per-anchor mean mm  | {_fmt(pre_gm):>13} | {_fmt(post_gm):>12} | "
          f"{_fmt(post_gm - pre_gm, sign=True):>8} |")
    ps, qs = pre["gauge"]["pooled_slope_pct"], post["gauge"]["pooled_slope_pct"]
    print(f"| gauge slope %       | {_fmt(ps, sign=True, nd=2):>13} | "
          f"{_fmt(qs, sign=True, nd=2):>12} | {_fmt(qs - ps, sign=True, nd=2):>8} |")
    pc, qc = pre["gauge"]["common_mode_a_mm"], post["gauge"]["common_mode_a_mm"]
    print(f"| common-mode mm      | {_fmt(pc, sign=True):>13} | "
          f"{_fmt(qc, sign=True):>12} | {_fmt(qc - pc, sign=True):>8} |")

    print("\n## Per-anchor mean measured range (mm)")
    print("| anchor | " + " | ".join(LBL) + " |")
    print("|" + "---|" * (len(LBL) + 1))
    print("| pre  | " + " | ".join(_fmt(pre["per_anchor_mean"][a], nd=0) for a in range(8)) + " |")
    print("| post | " + " | ".join(_fmt(post["per_anchor_mean"][a], nd=0) for a in range(8)) + " |")

    print("\n## Post-APS011 proxy gate (same thresholds as pg_pipeline)")
    g = post["gate"]; b = g["best"]; ba = g["best_auc"]
    print(f"| metric               | value |")
    print(f"|----------------------|-------|")
    print(f"| LOO |residual| median| {post['loo_abs_median']:.0f} mm |")
    if b is None:
        print(f"| best feature         | n/a (insufficient joined rows) |")
        print(f"| n pooled             | {g['npool']} ({g['n_big']} big-error) |")
        print(f"| VERDICT              | {g['verdict']} |")
        print("\n(Gate needs a proper walk with the SAME 8-anchor geometry; this "
              "scan produced too few CIR+LOO joins to compute partial Spearman.)")
    else:
        print(f"| best feature         | {b['feature']} |")
        print(f"| best partial rho     | {b['partial_rho']:+.4f} (p={b['partial_p']:.3g}) |")
        print(f"| best AUC (|e|>250mm)  | {ba['auc_disc']:.4f} ({ba['feature']}) |")
        print(f"| n pooled             | {g['npool']} ({g['n_big']} big-error) |")
        print(f"| VERDICT              | {g['verdict']} |")
    print("\n(GO if any |partial_rho|>=0.30 & p<0.01, or auc_disc>=0.70; "
          "NO-GO if best |rho|<0.15 & best auc_disc<0.60; else UNDERPOWERED.)")

    tot = os.cpu_count()
    print(f"\n## Compute: {tot} logical CPUs; Pool({nproc}). "
          f"LOO child-CPU pre {pre['cpu']['child_cpu_s']:.2f}s/"
          f"{pre['cpu']['wall_s']:.2f}s wall -> {pre['cpu']['cores_busy']:.1f} cores; "
          f"post {post['cpu']['child_cpu_s']:.2f}s/{post['cpu']['wall_s']:.2f}s -> "
          f"{post['cpu']['cores_busy']:.1f} cores busy.")


def _jsonable(pre, post):
    def strip(r):
        r = dict(r)
        r["gate"] = {k: v for k, v in r["gate"].items() if k != "table"}
        # keep only scalar summaries of best/best_auc
        for key in ("best", "best_auc"):
            bb = r["gate"][key]
            r["gate"][key] = (None if bb is None else
                              {k: bb[k] for k in ("feature", "partial_rho",
                               "partial_p", "auc_disc")})
        r["per_anchor_mean"] = {str(k): v for k, v in r["per_anchor_mean"].items()}
        return r
    return dict(pre=strip(pre), post=strip(post))


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pre_scan", help="pre-APS011 scan.log")
    ap.add_argument("post_scan", help="post-APS011 scan.log")
    ap.add_argument("--pglib", default=DEFAULT_PGLIB,
                    help="dir containing the locked pg_lib.py (default: the 8-anchor analysis dir)")
    ap.add_argument("--nproc", type=int, default=8, help="LOO worker processes (default 8)")
    ap.add_argument("--out", default=os.path.join(REPO, "analysis", "post_aps011_comparison.json"))
    ap.add_argument("--check-verdict", default="",
                    help="path to a gate_verdict.json; assert the PRE run reproduces it")
    args = ap.parse_args()

    # bind the locked pg_lib primitives into module globals (single source of truth)
    sys.path.insert(0, args.pglib)
    import pg_lib as L
    g = globals()
    g["_parse_log"] = L.parse_log
    g["_valid_ids"] = L.valid_ids
    g["_valid_range"] = L.valid_range
    g["_solve_pos"] = L.solve_pos
    g["_in_room_box"] = L.in_room_box
    g["_cir_features"] = L.cir_features
    g["_partial_spearman"] = L.partial_spearman
    g["_partial_spearman_boot"] = L.partial_spearman_boot
    g["_auc_score"] = L.auc_score
    g["_raw_spearman"] = L.raw_spearman
    g["_FEATURES"] = L.FEATURES

    for p in (args.pre_scan, args.post_scan):
        if not os.path.exists(p):
            sys.exit(f"scan log not found: {p}")

    P, LBL, DLY, W, Wc = L.load_geometry()
    t0 = time.time()
    print(f"[pre ] running pipeline on {args.pre_scan} ...")
    pre = run_pipeline(args.pre_scan, P, args.nproc)
    print(f"[post] running pipeline on {args.post_scan} ...")
    post = run_pipeline(args.post_scan, P, args.nproc)

    print_report(pre, post, LBL, args.nproc)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fp:
        json.dump(_jsonable(pre, post), fp, indent=2, default=float)
    print(f"\n[done] {time.time()-t0:.1f}s total. Wrote {args.out}")

    if args.check_verdict:
        ref = json.load(open(args.check_verdict))
        ok = True
        checks = [
            ("verdict", ref["verdict"], pre["gate"]["verdict"]),
            ("best_feature", ref["best_feature"], pre["gate"]["best"]["feature"]),
            ("partial_rho", ref["partial_rho"], round(pre["gate"]["best"]["partial_rho"], 4)),
            ("auc", ref["auc"], round(pre["gate"]["best_auc"]["auc_disc"], 4)),
            ("common_mode_a_mm", ref["gauge"]["common_mode_a_mm"],
             round(pre["gauge"]["common_mode_a_mm"], 1)),
            ("pooled_slope_pct", ref["gauge"]["pooled_within_anchor_slope_pct"],
             round(pre["gauge"]["pooled_slope_pct"], 3)),
        ]
        print("\n## Self-check: PRE run vs stored gate_verdict.json")
        for name, exp, got in checks:
            match = (exp == got)
            ok &= match
            print(f"  {'OK ' if match else 'XX '} {name}: ref={exp} got={got}")
        print("  -> " + ("REPRODUCED (faithful)" if ok
                         else "MISMATCH — investigate before trusting post numbers"))
        sys.exit(0 if ok else 3)


if __name__ == "__main__":
    main()
