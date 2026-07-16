#!/usr/bin/env python3
"""Validation harness for the V5 layout solver + U5 tag-solver changes.

Runs three validation blocks against real captures and writes REPORT.md:

  1. LAYOUT (inter-anchor, pairs_all.csv): V4-IO vs V5 vs V5-scalelock --
     pair RMS, C/H bound status, common-mode, layout divergence from V4-IO.
  2. U5-HOST LOO (overnight static, subsampled): the rebuilt C core with LOO
     rejection OFF vs ON -- position stability (p2p std) + per-anchor rejection
     rate. Demonstrates LOO fires on the event anchors (B/E) not the stable ones.
  3. pg_lib DELAYS (baseline walk + person pair): LOO residual median with
     delays=0 vs delays=V4-IO d_anchor_mm, and the person-effect near/far delta.

Everything read-only except the REPORT.md / *.json it writes into this dir.
CPU: reported at run end. No GPU used (pure CPU trilateration).
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start")
HERE = Path(__file__).resolve().parent
CAL = REPO / "logs/system_calibration_20260710_233443"
PAIRS = CAL / "autopos/pairs_all.csv"
V4_JSON = CAL / "anchor_layout.json"
SO = REPO / "biospur_tag_positioning_offline_solver/c_core/build/libbiospur_tagpos.so"
PGDIR = REPO / "logs/geiger_scan_20260711_161258_8anchor/analysis"
OVERNIGHT = REPO / "logs/geiger_overnight_static_20260711/scan.log"
BASELINE = REPO / "logs/geiger_scan_20260711_161258_8anchor/scan.log"
PERSON = REPO / "logs/geiger_scan_20260711_post_aps011/scan_person.log"
CLEAN = REPO / "logs/geiger_scan_20260711_post_aps011/scan.log"

sys.path.insert(0, str(REPO / "autopos_pipeline/v5"))
sys.path.insert(0, str(PGDIR))

ANCH = "ABCDEFGH"
_RE_A = re.compile(r";a(\d)=(-?\d+)")


# ---------------------------------------------------------------------------
# C-core (U5-host) ctypes shim -- calls the rebuilt libbiospur_tagpos.so
# ---------------------------------------------------------------------------
class CConfig(ctypes.Structure):
    _fields_ = [(n, ctypes.c_int if n in ("method", "max_iters", "robust_loss") else ctypes.c_double)
                for n in ["method", "max_iters", "huber_k", "min_sigma_mm", "convergence_mm",
                          "max_step_mm", "quality_penalty_scale", "quality_penalty_cap",
                          "residual_ema_start_mm", "residual_penalty_scale", "residual_penalty_cap",
                          "reject_abs_threshold_mm", "reject_min_improvement_mm",
                          "reject_improvement_ratio", "temporal_prior_sigma_mm", "robust_loss",
                          "tukey_c", "huber_delta_mm", "rf_snr_ref", "rf_sigma_mult_cap"]]


class CResult(ctypes.Structure):
    _fields_ = [("status", ctypes.c_int), ("method", ctypes.c_int), ("n_input", ctypes.c_int),
                ("n_used", ctypes.c_int), ("iterations", ctypes.c_int), ("rejected_index", ctypes.c_int),
                ("xyz_mm", ctypes.c_double * 3), ("residual_rms_mm", ctypes.c_double),
                ("residual_p95_abs_mm", ctypes.c_double), ("max_abs_residual_mm", ctypes.c_double)]


def _load_lib():
    lib = ctypes.CDLL(str(SO))
    pd = ctypes.POINTER(ctypes.c_double); pi = ctypes.POINTER(ctypes.c_int)
    lib.biospur_tagpos_default_config.argtypes = [ctypes.POINTER(CConfig), ctypes.c_int]
    lib.biospur_tagpos_default_config.restype = None
    lib.biospur_tagpos_solve_frame.argtypes = [pd, pd, pd, pd, pd, pd, pd, pd, ctypes.c_int, pd,
                                               ctypes.c_double, ctypes.POINTER(CConfig), pd, pd, pi,
                                               ctypes.POINTER(CResult)]
    lib.biospur_tagpos_solve_frame.restype = ctypes.c_int
    return lib


def _arr(x): x = list(x); return (ctypes.c_double * len(x))(*x)
def _iarr(x): x = list(x); return (ctypes.c_int * len(x))(*x)


def c_solve(lib, A, ranges, ids, cfg, sigmas, delays=None, x0=None, rf=None):
    """Solve one frame with the C core over anchors `ids`. Returns (xyz, result).

    rf: optional per-anchor RF first-path SNR (indexed by anchor id). Low SNR
    inflates sigma (>=1.0) in effective_sigma. NEVER drops an anchor (no LOO).
    """
    n = len(ids)
    axyz = _arr(A[ids].flatten())
    rng = _arr([ranges[a] for a in ids])
    sig = _arr([sigmas[a] for a in ids])
    dly = _arr([delays[a] for a in ids]) if delays is not None else None
    rfa = _arr([rf[a] for a in ids]) if rf is not None else None
    q = _arr([100.0] * n)
    out_xyz = _arr([0, 0, 0]); out_res = _arr([0.0] * n); out_used = _iarr([0] * n); res = CResult()
    x0a = _arr(x0) if x0 is not None else None
    rc = lib.biospur_tagpos_solve_frame(axyz, rng, dly, sig, q, q, _arr([0.0] * n), rfa,
                                        ctypes.c_int(n), x0a, ctypes.c_double(0.0),
                                        ctypes.byref(cfg), out_xyz, out_res, out_used, ctypes.byref(res))
    return (np.array(out_xyz) if rc == 0 else None), res


_RE_AID = re.compile(r";cir_aid=(\d+)")
_RE_CIR = re.compile(r";cir=([0-9A-Fa-f]+)")


def _decode_cir(hexstr):
    """1016-tap I/Q CIR hex -> magnitude array, or None if wrong length."""
    if len(hexstr) != 8128:
        return None
    iq = np.frombuffer(bytes.fromhex(hexstr), dtype="<i2").reshape(-1, 2).astype(float)
    return np.hypot(iq[:, 0], iq[:, 1])


# ---------------------------------------------------------------------------
# LSCAN parsing (stream, truncate at ;cir= for memory)
# ---------------------------------------------------------------------------
def parse_lscan(path, stride=1, limit=None):
    """Yield per-line dict {aid: range_mm} for valid (>0) anchors. stride subsamples."""
    frames = []
    with open(path, "r", errors="ignore") as f:
        i = 0
        for line in f:
            if "LSCAN" not in line:
                continue
            if i % stride == 0:
                cut = line.split(";cir=", 1)[0]
                rg = {int(a): int(v) for a, v in _RE_A.findall(cut) if 0 <= int(a) < 8 and int(v) > 0}
                frames.append(rg)
                if limit and len(frames) >= limit:
                    break
            i += 1
    return frames


# ===========================================================================
# Block 1 -- layout comparison
# ===========================================================================
def block_layout():
    import solve_v5 as V5
    v4 = json.loads(V4_JSON.read_text())
    v4x = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in v4["anchors"]])
    v4d = np.array([a["d_anchor_mm"] for a in v4["anchors"]])
    aids = list(range(8))
    pd_, directed, fc = V5.load_pairs_and_series(PAIRS, aids)
    init, _ = V5.EVAL.solve_autopos_v1(pd_, aids)

    def maxdev(x): return float(np.max(np.linalg.norm(x - v4x, axis=1)))

    rows = []
    # V4-IO baseline (from deployed json)
    rows.append({"solver": "V4-IO (deployed)", "pair_rms_mm": v4["stats"]["inter_anchor_pair_rms_mm"],
                 "common_mode_mm": 0.0, "C_delay": float(v4d[2]), "H_delay": float(v4d[7]),
                 "n_at_bound": "C,H at +60", "maxdev_v4io_mm": 0.0, "scale_identifiable": True})
    # V5 spec config
    x, dly, r = V5.solve_v5(pd_, aids, init)
    rows.append({"solver": "V5 (spec: c+/-150 e+/-200)", "pair_rms_mm": round(r.pair_rmse_mm, 2),
                 "common_mode_mm": round(r.common_mode_mm, 1),
                 "C_delay": round(float(r.differential_delay_mm[2]), 1),
                 "H_delay": round(float(r.differential_delay_mm[7]), 1),
                 "n_at_bound": f"c@bound={r.c_at_bound}, e@bound={r.n_e_at_bound}",
                 "maxdev_v4io_mm": round(maxdev(x), 1),
                 "scale_identifiable": not r.c_at_bound})
    # V5 scale-lock
    x2, dly2, r2 = V5.solve_v5(pd_, aids, init, fix_common_mode=0.0, e_bound_mm=60.0)
    rows.append({"solver": "V5 (scale-lock: c=0 e+/-60)", "pair_rms_mm": round(r2.pair_rmse_mm, 2),
                 "common_mode_mm": 0.0,
                 "C_delay": round(float(r2.differential_delay_mm[2]), 1),
                 "H_delay": round(float(r2.differential_delay_mm[7]), 1),
                 "n_at_bound": f"e@bound={r2.n_e_at_bound}",
                 "maxdev_v4io_mm": round(maxdev(x2), 1), "scale_identifiable": True})
    stability = V5.classify_stability(directed, aids)
    stab = {ANCH[a]: stability[a]["class"] for a in aids}
    return {"rows": rows, "stability_calib": stab}


# ===========================================================================
# Block 2 -- RF-informed sigma on overnight static (NO LOO, never drop anchors)
#   (a) RF-metric DISCRIMINATION: does CIR first-path SNR separate the event
#       anchors (B,E,H) from the stable ones?  (validates the RF metric itself)
#   (b) RF-informed sigma vs flat uniform sigma=25 -- position stability, all 8
#       anchors always kept.
# ===========================================================================
def block_overnight_rf(stride=40):
    import pg_lib as pg
    lib = _load_lib()
    v4 = json.loads(V4_JSON.read_text())
    A = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in v4["anchors"]])
    BASE_SIGMA = 25.0
    cfg = CConfig(); lib.biospur_tagpos_default_config(ctypes.byref(cfg), 1)

    # Stream overnight: subsample; decode the per-line cir_aid anchor's CIR -> SNR_fp.
    frames = []                       # list of {aid:range}
    snr_by_anchor = {a: [] for a in range(8)}
    t0 = time.time()
    with open(OVERNIGHT, "r", errors="ignore") as f:
        i = 0
        for line in f:
            if "LSCAN" not in line:
                continue
            if i % stride == 0:
                cut = line.split(";cir=", 1)[0]
                rg = {int(a): int(v) for a, v in _RE_A.findall(cut) if 0 <= int(a) < 8 and int(v) > 0}
                m_aid = _RE_AID.search(cut); m_cir = _RE_CIR.search(line)
                if m_aid and m_cir:
                    aid = int(m_aid.group(1))
                    mag = _decode_cir(m_cir.group(1))
                    if mag is not None and aid in rg:
                        snr = float(pg.cir_features(mag, rg[aid]).get("SNR_fp", np.nan))
                        if np.isfinite(snr):
                            snr_by_anchor[aid].append(snr)
                if len(rg) >= 5:
                    frames.append(rg)
            i += 1
    parse_s = time.time() - t0

    # (a) discrimination: per-anchor median first-path SNR from the CIR
    med_snr = {a: (float(np.median(snr_by_anchor[a])) if snr_by_anchor[a] else float("nan"))
               for a in range(8)}
    # RF-quality estimate fed to the solver: each anchor's median CIR SNR.
    rf_est = [med_snr[a] if np.isfinite(med_snr[a]) else float("inf") for a in range(8)]

    # (b) flat sigma=25 vs RF-informed sigma; both keep all 8 anchors (no LOO).
    pos_flat, pos_rf = [], []
    xf = xr = None
    for rg in frames:
        ids = sorted(rg)
        sig = [BASE_SIGMA] * 8
        xf2, _ = c_solve(lib, A, rg, ids, cfg, sig, x0=xf)              # flat
        xr2, _ = c_solve(lib, A, rg, ids, cfg, sig, x0=xr, rf=rf_est)   # RF-informed sigma
        if xf2 is not None:
            pos_flat.append(xf2); xf = xf2
        if xr2 is not None:
            pos_rf.append(xr2); xr = xr2
    pos_flat = np.array(pos_flat); pos_rf = np.array(pos_rf)

    def p2p(p): return [round(v, 1) for v in (np.percentile(p, 97.5, axis=0) - np.percentile(p, 2.5, axis=0))]
    def stdev(p): return [round(v, 1) for v in np.std(p, axis=0)]

    # rank anchors by SNR to show B/E/H are the low-SNR (event) anchors
    ranked = sorted(range(8), key=lambda a: (med_snr[a] if np.isfinite(med_snr[a]) else 1e9))
    return {
        "n_frames": len(frames), "stride": stride, "parse_s": round(parse_s, 1),
        "cir_frames_per_anchor": {ANCH[a]: len(snr_by_anchor[a]) for a in range(8)},
        "median_fp_snr": {ANCH[a]: round(med_snr[a], 2) for a in range(8)},
        "snr_rank_low_to_high": "".join(ANCH[a] for a in ranked),
        "rf_sigma_multiplier": {ANCH[a]: round(max(1.0, min(10.0, 10.0 / max(med_snr[a], 1.0))), 2)
                                if np.isfinite(med_snr[a]) else 1.0 for a in range(8)},
        "pos_std_flat_mm": stdev(pos_flat),
        "pos_std_rf_mm": stdev(pos_rf),
        "pos_p2p95_flat_mm": p2p(pos_flat),
        "pos_p2p95_rf_mm": p2p(pos_rf),
        "note": "no LOO: all 8 anchors kept every frame; RF only scales sigma (>=1x).",
    }


# ===========================================================================
# Block 3 -- pg_lib delays (baseline walk + person effect)
# ===========================================================================
def _loo_residuals(pg, P, frames, delays=None):
    """Median |LOO residual| per anchor over frames (leave-one-out trilateration)."""
    resid = {a: [] for a in range(8)}
    x0 = P.mean(0)
    for rg in frames:
        ids = [a for a in sorted(rg) if pg.valid_range(rg[a])]
        if len(ids) < 5:
            continue
        s, _, _ = pg.solve_pos(P, rg, x0, ids=ids, delays=delays)
        if s is None:
            continue
        x0 = s
        for a in ids:
            rem = [b for b in ids if b != a]
            sl, _, _ = pg.solve_pos(P, rg, s, ids=rem, delays=delays)
            if sl is not None:
                pred = np.linalg.norm(P[a] - sl)
                meas = rg[a] - (delays.get(a, 0.0) if isinstance(delays, dict) else 0.0)
                resid[a].append(meas - pred)
    return {a: (float(np.median(np.abs(resid[a]))) if resid[a] else float("nan")) for a in range(8)}


def block_pglib_delays():
    import pg_lib as pg
    P, LBL, DLY, W, w0 = pg.load_geometry()
    delays = {a: float(DLY[a]) for a in range(len(DLY)) if np.isfinite(DLY[a])}

    out = {}
    # Baseline walk: LOO residual median with/without delays
    base = pg.parse_log(str(BASELINE), want_cir=False)
    bf = [f["rng"] for f in base]
    r_nodelay = _loo_residuals(pg, P, bf, delays=None)
    r_delay = _loo_residuals(pg, P, bf, delays=delays)
    out["baseline_walk"] = {
        "n_frames": len(bf),
        "loo_median_abs_no_delay_mm": {ANCH[a]: round(r_nodelay[a], 1) for a in range(8)},
        "loo_median_abs_with_delay_mm": {ANCH[a]: round(r_delay[a], 1) for a in range(8)},
    }

    # Person effect: near (BCFG=1,2,5,6) vs far (ADEH=0,3,4,7)
    NEAR, FAR = [1, 2, 5, 6], [0, 3, 4, 7]
    pf = [f["rng"] for f in pg.parse_log(str(PERSON), want_cir=False)]
    cf = [f["rng"] for f in pg.parse_log(str(CLEAN), want_cir=False)]
    rp = _loo_residuals(pg, P, pf, delays=delays)
    rc = _loo_residuals(pg, P, cf, delays=delays)
    near_delta = np.nanmean([rp[a] - rc[a] for a in NEAR])
    far_delta = np.nanmean([rp[a] - rc[a] for a in FAR])
    out["person_effect"] = {
        "n_person": len(pf), "n_clean": len(cf),
        "loo_median_person_mm": {ANCH[a]: round(rp[a], 1) for a in range(8)},
        "loo_median_clean_mm": {ANCH[a]: round(rc[a], 1) for a in range(8)},
        "near_BCFG_delta_mm": round(float(near_delta), 1),
        "far_ADEH_delta_mm": round(float(far_delta), 1),
        "near_minus_far_mm": round(float(near_delta - far_delta), 1),
    }
    return out


# ===========================================================================
def main():
    t0 = time.time()
    print(f"[validate] CPU: {os.cpu_count()} logical cores")
    results = {"cpu_count": os.cpu_count()}
    print("[validate] Block 1: layout comparison...")
    results["layout"] = block_layout()
    print("[validate] Block 2: RF-informed sigma + CIR discrimination on overnight...")
    # stride coprime with 8 so cir_aid round-robin covers all 8 anchors evenly
    results["overnight_rf"] = block_overnight_rf(stride=41)
    print("[validate] Block 3: pg_lib delays (baseline walk + person)...")
    results["pglib_delays"] = block_pglib_delays()
    results["wallclock_s"] = round(time.time() - t0, 1)
    (HERE / "validation_results.json").write_text(json.dumps(results, indent=2))
    print(f"[validate] done in {results['wallclock_s']}s -> validation_results.json")
    return results


if __name__ == "__main__":
    main()
