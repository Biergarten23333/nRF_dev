#!/usr/bin/env python3
"""Shared library for the Geiger MODE_SCAN PROXY-GATE analysis (P1 + P2).

Everything here is pure/functional so the pipeline (pg_pipeline.py) and any
notebook can import it.  Method constraints (from the brief / prior failures):
  - LOO residuals ONLY as the error label (no self-leakage).
  - Partial Spearman controlling {solved_range, anchor_id, n_anchors}.
  - Locked CIR feature set (do NOT add features after seeing correlations).
"""
import os, re, json
import numpy as np
from scipy.optimize import least_squares
from scipy.stats import rankdata, kurtosis, spearmanr, t as tdist

# --------------------------------------------------------------------------
# constants / paths
# --------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
CAP  = os.path.dirname(HERE)                       # .../geiger_scan_..._8anchor
REPO = os.path.abspath(os.path.join(CAP, "..", ".."))
LOG  = os.path.join(CAP, "scan.log")
CAL  = os.path.join(REPO, "logs", "system_calibration_20260710_233443")
LAYOUT = os.path.join(CAL, "anchor_layout.json")
WAND   = os.path.join(CAL, "wand_positions.json")
FIGDIR = os.path.join(HERE, "figures")

NTAP    = 1016
TAP_NS  = 1.0016                                   # DW1000 accumulator sample period
TAP_M   = 299792458 * TAP_NS * 1e-9                # ~0.3003 m / tap
RUN_S   = 120.0                                    # capture duration (approx)
VALID_LO, VALID_HI = 300, 8000                     # valid range gate (mm)

# CIR analysis windows (locked)
NOISE_LO, NOISE_HI = 100, 600                      # pure-noise taps (FP sits ~750)
FP_SEARCH_START    = 700                            # search FP forward from here
FP_WIN             = 64                             # analysis window after FP


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------
def load_geometry():
    aj = json.load(open(LAYOUT))
    P   = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in aj["anchors"]], float)
    LBL = [a["label"] for a in aj["anchors"]]
    DLY = np.array([a.get("d_anchor_mm", np.nan) for a in aj["anchors"]], float)  # AutoPos fitted delay
    wj = json.load(open(WAND))
    W  = np.array([v["position_mm"] for v in wj["wand_tags"].values()], float)
    return P, LBL, DLY, W, W.mean(0)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
_RE_A   = re.compile(r';a(\d)=(-?\d+)')
_RE_AID = re.compile(r';cir_aid=(\d+);')
_RE_DG  = re.compile(r';rcph=(\d+);rxtofs=(-?\d+);ttcki=(\d+);agc=(\d+);')
_RE_CIR = re.compile(r';cir=([0-9A-Fa-f]+)')

def parse_log(path=LOG, want_cir=True):
    """Return list of dicts: {rng:{aid:mm}, cir_aid, diag:(rcph,rxtofs,ttcki,agc), cir:mag[1016] or None}."""
    rows = []
    for L in open(path):
        if not L.startswith("LSCAN;"):
            continue
        rg  = {int(m.group(1)): int(m.group(2)) for m in _RE_A.finditer(L)}
        cm  = _RE_AID.search(L)
        dm  = _RE_DG.search(L)
        cir = None
        if want_cir:
            hm = _RE_CIR.search(L)
            if hm and len(hm.group(1)) == 8128:
                iq = np.frombuffer(bytes.fromhex(hm.group(1)), dtype="<i2").astype(float).reshape(NTAP, 2)
                cir = np.hypot(iq[:, 0], iq[:, 1])          # magnitude taps
        rows.append(dict(rng=rg,
                         cir_aid=int(cm.group(1)) if cm else None,
                         diag=tuple(int(x) for x in dm.groups()) if dm else None,
                         cir=cir))
    return rows

def valid_range(mm):
    return VALID_LO <= mm <= VALID_HI

def valid_ids(rg):
    return [a for a in sorted(rg) if valid_range(rg[a])]


# --------------------------------------------------------------------------
# trilateration
# --------------------------------------------------------------------------
def solve_pos(P, rg, x0, ids=None, delays=None, estimate_d_tag=False,
              d_tag_prior_sigma_mm=300.0):
    """LM least-squares position on responding anchors (>=4).

    Default (estimate_d_tag=False): estimates 3 unknowns [x, y, z] and returns
    ``(pos|None, ids, rms)`` -- byte-for-byte the previous behavior.

    With estimate_d_tag=True: co-estimates a 4th unknown ``d_tag`` (a per-tag
    common-mode range offset from that tag's DW1000 antenna delay) and returns a
    4-tuple ``(pos|None, ids, rms, d_tag)``. The model residual becomes
    ``||P_i - x|| + delay_i + d_tag - range_i`` and a soft prior row
    ``d_tag / d_tag_prior_sigma_mm`` is appended (default sigma 300 mm, APS014
    uncalibrated 3-sigma). The prior keeps d_tag from aliasing with radial
    position when anchor geometry is poor; with the tag inside the anchor convex
    hull the coupling is weak and the prior barely activates. The reported rms
    EXCLUDES the prior row (``r.fun[:-1]``).

    delays: optional per-anchor delay correction. Accepts a dict {anchor_id:
        delay_mm} or an array indexable by anchor_id. A positive d_anchor_mm
        (anchor reads LONG) is added to the predicted side / subtracted from the
        measured range -- identical either way. delays=None => no correction.
    """
    if ids is None:
        ids = valid_ids(rg)
    if len(ids) < 4:
        return (None, ids, np.nan, np.nan) if estimate_d_tag else (None, ids, np.nan)
    Pi = P[ids]
    di = np.array([rg[a] for a in ids], float)
    if delays is not None:
        if isinstance(delays, dict):
            corr = np.array([float(delays.get(a, 0.0)) for a in ids], float)
        else:
            corr = np.array([float(delays[a]) for a in ids], float)
    else:
        corr = np.zeros(len(ids))

    if estimate_d_tag:
        inv_prior = 1.0 / float(d_tag_prior_sigma_mm)

        def resid(state):
            pos = state[:3]
            d_tag = state[3]
            dists = np.linalg.norm(Pi - pos, axis=1)
            r = dists + corr + d_tag - di            # ||P_i - x|| + delay_i + d_tag - range_i
            return np.append(r, d_tag * inv_prior)   # soft prior on d_tag
        x_full = np.r_[np.asarray(x0, float), 0.0]
        res = least_squares(resid, x_full, method="lm", max_nfev=200)
        rms = float(np.sqrt(np.mean(res.fun[:-1] ** 2)))
        return res.x[:3], ids, rms, float(res.x[3])

    di = di - corr                                   # 3-unknown path (unchanged)
    r = least_squares(lambda x: np.linalg.norm(Pi - x, axis=1) - di, x0,
                      method="lm", max_nfev=200)
    return r.x, ids, float(np.sqrt(np.mean(r.fun ** 2)))

def in_room_box(s):
    return (-2500 < s[0] < 7500) and (-3000 < s[1] < 6000) and (-6000 < s[2] < 1500)


# --------------------------------------------------------------------------
# CIR feature extraction  (LOCKED SET -- do not modify after seeing rho)
# --------------------------------------------------------------------------
def cir_features(mag, range_mm):
    """Compute the locked per-frame CIR features. Returns dict (friis_residual added later,
    per-anchor, in the pipeline). Uses software LDE first-path detection."""
    # noise floor: robust sigma from MAD of pure-noise taps
    noise = mag[NOISE_LO:NOISE_HI]
    med   = np.median(noise)
    mad   = np.median(np.abs(noise - med))
    sigma = 1.4826 * mad + 1e-9

    peak = mag.max()
    thr  = max(6.0 * sigma, 0.25 * peak)

    # first-path = first tap above thr, searching forward from FP_SEARCH_START
    seg  = mag[FP_SEARCH_START:]
    above = np.where(seg > thr)[0]
    if len(above) == 0:                              # fallback: global peak
        fp = int(np.argmax(mag))
    else:
        fp = FP_SEARCH_START + int(above[0])
    fp = int(np.clip(fp, FP_SEARCH_START, NTAP - FP_WIN - 1))

    # sub-tap parabolic interpolation around fp
    if 0 < fp < NTAP - 1:
        a_, b_, c_ = mag[fp - 1], mag[fp], mag[fp + 1]
        den = (a_ - 2 * b_ + c_)
        subtap = fp + (0.5 * (a_ - c_) / den if den != 0 else 0.0)
    else:
        subtap = float(fp)

    # rise start: first tap above 1*sigma searching forward from FP_SEARCH_START
    rise_above = np.where(seg > sigma)[0]
    rise_start = FP_SEARCH_START + int(rise_above[0]) if len(rise_above) else fp
    rise_time  = fp - rise_start

    win  = mag[fp:fp + FP_WIN]
    tau  = np.arange(FP_WIN, dtype=float)            # taps relative to fp (>=0)
    p    = win ** 2
    psum = p.sum() + 1e-12
    fp_mag = mag[fp]

    rms_ds = np.sqrt((tau ** 2 * p).sum() / psum) * TAP_NS          # ns
    early  = (win[:3] ** 2).sum() / psum
    pre    = mag[fp - 10:fp - 2].max() / (fp_mag + 1e-9) if fp >= 10 else np.nan
    kurt   = float(kurtosis(win))                                    # Fisher
    relpow = psum / ((noise ** 2).sum() + 1e-12)

    return dict(
        fp_tap=fp, fp_subtap=subtap, sigma_noise=sigma, peak=peak, fp_mag=fp_mag,
        SNR_fp=fp_mag / sigma,
        FP_PK_ratio=fp_mag / (peak + 1e-9),
        rise_time=float(rise_time),
        RMS_delay_spread=rms_ds,
        early_ratio=early,
        pre_fp_leak=pre,
        kurtosis=kurt,
        relative_power=relpow,
        range_mm=float(range_mm) if range_mm is not None else np.nan,
    )

# ordered locked feature list used everywhere downstream
FEATURES = ["SNR_fp", "FP_PK_ratio", "rise_time", "RMS_delay_spread",
            "early_ratio", "pre_fp_leak", "kurtosis", "relative_power",
            "friis_residual"]


# --------------------------------------------------------------------------
# statistics: partial Spearman + AUC (no sklearn)
# --------------------------------------------------------------------------
def _design(controls):
    """Build OLS design matrix [1, rank(cont0), rank(cont1), ..., onehot(cat)]
    controls: dict with keys 'cont' -> list of 1d arrays (rank-transformed),
              'cat'  -> 1d integer array (one-hot, drop last level)."""
    n = None
    cols = []
    for c in controls.get("cont", []):
        r = rankdata(c).astype(float)
        cols.append(r)
        n = len(r)
    cat = controls.get("cat", None)
    if cat is not None:
        levels = np.unique(cat)
        for lv in levels[:-1]:                       # drop last -> avoid collinearity w/ intercept
            cols.append((cat == lv).astype(float))
        n = len(cat)
    X = np.column_stack([np.ones(n)] + cols) if cols else np.ones((n, 1))
    return X

def _residualize(y, X):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return y - X @ beta

def partial_spearman(feat, err, controls):
    """Partial Spearman rho(feat, err | controls). Returns (rho, p, n, dof)."""
    feat = np.asarray(feat, float); err = np.asarray(err, float)
    ok = np.isfinite(feat) & np.isfinite(err)
    for c in controls.get("cont", []):
        ok &= np.isfinite(np.asarray(c, float))
    if controls.get("cat") is not None:
        ok &= np.isfinite(np.asarray(controls["cat"], float))
    feat, err = feat[ok], err[ok]
    ctl = {"cont": [np.asarray(c, float)[ok] for c in controls.get("cont", [])]}
    if controls.get("cat") is not None:
        ctl["cat"] = np.asarray(controls["cat"])[ok]
    n = ok.sum()
    if n < 10:
        return np.nan, np.nan, int(n), 0
    X  = _design(ctl)
    ry = _residualize(rankdata(feat).astype(float), X)
    re = _residualize(rankdata(err).astype(float), X)
    sy, se = ry.std(), re.std()
    if sy < 1e-12 or se < 1e-12:
        return np.nan, np.nan, int(n), 0
    rho = float(np.corrcoef(ry, re)[0, 1])
    dof = int(n - X.shape[1] - 1)                    # n - controls - 2
    if dof <= 0 or abs(rho) >= 1:
        return rho, np.nan, int(n), dof
    tval = rho * np.sqrt(dof / (1 - rho ** 2))
    p = 2 * tdist.sf(abs(tval), dof)
    return rho, float(p), int(n), dof

def partial_spearman_boot(feat, err, controls, nboot=1000, seed=12345):
    """Bootstrap 95% CI on the partial rho. Deterministic seed (no wall-clock)."""
    feat = np.asarray(feat, float); err = np.asarray(err, float)
    cont = [np.asarray(c, float) for c in controls.get("cont", [])]
    cat  = np.asarray(controls["cat"]) if controls.get("cat") is not None else None
    n = len(feat)
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(nboot):
        idx = rng.integers(0, n, n)
        cc = {"cont": [c[idx] for c in cont]}
        if cat is not None:
            cc["cat"] = cat[idx]
        rho, _p, nn, _d = partial_spearman(feat[idx], err[idx], cc)
        if np.isfinite(rho):
            out.append(rho)
    if not out:
        return np.nan, np.nan
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))

def auc_score(scores, pos_mask):
    """ROC-AUC via Mann-Whitney U. AUC = P(score_pos > score_neg). Oriented as given."""
    scores = np.asarray(scores, float)
    ok = np.isfinite(scores)
    scores, pos_mask = scores[ok], np.asarray(pos_mask, bool)[ok]
    npos, nneg = int(pos_mask.sum()), int((~pos_mask).sum())
    if npos == 0 or nneg == 0:
        return np.nan
    r = rankdata(scores)
    U = r[pos_mask].sum() - npos * (npos + 1) / 2.0
    return float(U / (npos * nneg))

def raw_spearman(feat, err):
    feat = np.asarray(feat, float); err = np.asarray(err, float)
    ok = np.isfinite(feat) & np.isfinite(err)
    if ok.sum() < 5:
        return np.nan, np.nan
    r, p = spearmanr(feat[ok], err[ok])
    return float(r), float(p)
