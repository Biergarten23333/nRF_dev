#!/usr/bin/env python3
"""AutoPos B & H focused diagnostic (Tasks 3-6).

Given three inter-anchor sweeps (overnight / field / fresh), for each of B and H:
  - pairwise range stats (median, MAD, N) per link + delta vs prior sweeps
  - directional-bias pattern (azimuth vs residual, sinusoid fit)
  - clean-6 consistency (multilaterate vs fixed clean-6, per-link residual, RMS)
  - temporal stability (4 chunks of the anchor's own round -> position drift)
and a full 8-anchor comparison table (per-anchor multilat RMS vs clean-6).

Clean-6 = A,C,D,E,F,G (self-consistent 39mm core). B,H are the flagged anchors.
Frame: clean-6 solved by metric MDS + GN, Procrustes-aligned to the v4-io
reference layout so coordinates match layout_clean6.json / layout_full8.json.
"""
import json
import os
import statistics
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start"
V4IO_REF = os.path.join(REPO, "autopos_pipeline/erlangen_20260528_mocap/"
                              "solver/outputs/v4io_field_check/v4-io/layout.json")
CLEAN = list("ACDEFG")
LABELS = list("ABCDEFGH")


# ----------------------------- data loading -----------------------------
def load_directed(summary_path):
    """Return directed[(master,peer)] = [dist_mm, ...] in time order."""
    d = json.load(open(summary_path))
    D = {}
    for rk in sorted(d.get("rounds", {})):
        r = d["rounds"][rk]
        for ln in r.get("sw_lines", []):
            if "SW-" not in ln:
                continue
            body = ln.split("SW-", 1)[1]
            t = body.split(",")
            m = t[0].strip()
            i = 1
            while i + 1 < len(t):
                peer = t[i].strip()
                try:
                    dist = int(float(t[i + 1]))
                    q = int(float(t[i + 2])) if i + 2 < len(t) else 0
                except (ValueError, IndexError):
                    break
                if peer and dist > 0 and q > 0:
                    D.setdefault((m, peer), []).append(dist)
                i += 3
    return D


def med(vals):
    return float(statistics.median(vals)) if vals else None


def mad(vals):
    if not vals:
        return None
    m = statistics.median(vals)
    return float(statistics.median([abs(v - m) for v in vals]))


def undirected_vals(D, a, b):
    return list(D.get((a, b), [])) + list(D.get((b, a), []))


def undirected_med(D, a, b):
    v = undirected_vals(D, a, b)
    return med(v) if v else None


# ----------------------------- clean-6 solve -----------------------------
def solve_mds(labels, D):
    n = len(labels)
    ii = {c: k for k, c in enumerate(labels)}
    M = np.zeros((n, n))
    for a in labels:
        for b in labels:
            if a != b:
                mv = undirected_med(D, a, b)
                M[ii[a], ii[b]] = mv if mv is not None else 0.0
    M = (M + M.T) / 2
    D2 = M ** 2
    J = np.eye(n) - np.ones((n, n)) / n
    B = -0.5 * J @ D2 @ J
    w, V = np.linalg.eigh(B)
    o = np.argsort(w)[::-1]
    X = V[:, o][:, :3] * np.sqrt(np.clip(w[o][:3], 0, None))
    for _ in range(3000):
        g = np.zeros_like(X)
        for i in range(n):
            for j in range(i + 1, n):
                diff = X[i] - X[j]
                rng = np.linalg.norm(diff) + 1e-9
                res = rng - M[i, j]
                u = diff / rng
                g[i] += res * u
                g[j] -= res * u
        X -= 0.03 * g
    res = np.array([np.linalg.norm(X[i] - X[j]) - M[i, j]
                    for i in range(n) for j in range(i + 1, n)])
    return {labels[i]: X[i] for i in range(n)}, float(np.sqrt(np.mean(res ** 2)))


def procrustes(src, dst):
    S = np.array(src)
    Dd = np.array(dst)
    cs = S.mean(0)
    cd = Dd.mean(0)
    H = (S - cs).T @ (Dd - cd)
    U, _, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if np.linalg.det(R) < 0:
        Vt[-1] *= -1
        R = Vt.T @ U.T
    t = cd - R @ cs
    return R, t


def clean6_frame(D, clean=CLEAN):
    pos6, rms6 = solve_mds(clean, D)
    v4 = {a["label"]: np.array([a["x_mm"], a["y_mm"], a["z_mm"]])
          for a in json.load(open(V4IO_REF))["anchors"]}
    R, t = procrustes([pos6[l] for l in clean], [v4[l] for l in clean])
    pos6 = {l: R @ pos6[l] + t for l in clean}
    return pos6, rms6


# ----------------------------- multilateration -----------------------------
def robust_place(pos_ref, links, huber=120.0, iters=300, x0=None):
    """links: [(label, dist_mm)]. Huber-robust 3D position vs fixed anchors."""
    A = np.array([pos_ref[l] for l, _ in links])
    dv = np.array([d for _, d in links], dtype=float)
    x = A.mean(0) if x0 is None else np.array(x0, dtype=float)
    w = np.ones(len(dv))
    for _ in range(iters):
        diff = x - A
        rng = np.linalg.norm(diff, axis=1) + 1e-9
        res = rng - dv
        a = np.abs(res)
        w = np.where(a <= huber, 1.0, huber / np.maximum(a, 1e-9))
        Jm = diff / rng[:, None]
        WJ = Jm * w[:, None]
        try:
            dx = np.linalg.lstsq(WJ.T @ Jm, -(WJ.T @ res), rcond=None)[0]
        except np.linalg.LinAlgError:
            break
        x = x + dx
        if np.linalg.norm(dx) < 1e-7:
            break
    diff = x - A
    rng = np.linalg.norm(diff, axis=1)
    res = rng - dv           # predicted - measured
    resid = {links[k][0]: float(-res[k]) for k in range(len(links))}  # measured - predicted
    wrms = float(np.sqrt(np.sum(w * res ** 2) / np.sum(w)))
    rms = float(np.sqrt(np.mean(res ** 2)))
    return x, resid, wrms, rms, {links[k][0]: float(w[k]) for k in range(len(links))}


def robust_multistart(pos_ref, links, huber=120.0, seeds=None):
    """Multi-start multilateration: robust to mirror / local-minimum basins.
    Seeds = centroid + each reference anchor + any provided seeds. Pick the
    basin where the MOST links agree (inliers), tie-broken by lowest wrms."""
    A = np.array([pos_ref[l] for l, _ in links])
    inits = [A.mean(0)] + [A[k] for k in range(len(A))]
    if seeds:
        inits += [np.array(s, dtype=float) for s in seeds]
    best = None
    for x0 in inits:
        x, resid, wrms, rms, wts = robust_place(pos_ref, links, huber=huber, x0=x0)
        n_in = sum(1 for v in resid.values() if abs(v) <= huber)
        key = (n_in, -wrms)
        if best is None or key > best[0]:
            best = (key, (x, resid, wrms, rms, wts))
    return best[1]


def basin_scan(pos_ref, links, seeds=None, dedupe_mm=150.0):
    """Enumerate DISTINCT multilateration basins from many seeds. Returns basins
    sorted by wrms. If >1 distinct basin with comparable wrms and different worst
    links, the target's position is ill-conditioned (mutually inconsistent ranges)."""
    A = np.array([pos_ref[l] for l, _ in links])
    inits = [A.mean(0)] + [A[k] for k in range(len(A))]
    if seeds:
        inits += [np.array(s, dtype=float) for s in seeds]
    sols = []
    for x0 in inits:
        x, resid, wrms, rms, wts = robust_place(pos_ref, links, x0=x0)
        worst = max(resid.items(), key=lambda kv: abs(kv[1]))
        sols.append({"pos": [round(float(v), 1) for v in x], "wrms": round(wrms, 1),
                     "rms": round(rms, 1), "worst_link": worst[0],
                     "worst_resid_mm": round(worst[1], 1),
                     "resid": {k: round(v, 1) for k, v in resid.items()}, "_x": x})
    sols.sort(key=lambda s: s["wrms"])
    distinct = []
    for s in sols:
        if all(np.linalg.norm(s["_x"] - d["_x"]) > dedupe_mm for d in distinct):
            distinct.append(s)
    for d in distinct:
        d.pop("_x", None)
    return distinct


def multilat_vs_clean6(pos6, D, target, clean=CLEAN, seeds=None):
    """Undirected medians target<->clean. Returns pos, resid dict, wrms, rms."""
    links = []
    for l in clean:
        mv = undirected_med(D, target, l)
        if mv is not None:
            links.append((l, mv))
    if len(links) < 4:
        return None
    x, resid, wrms, rms, wts = robust_multistart(pos6, links, seeds=seeds)
    return {"pos": x, "resid": resid, "wrms": round(wrms, 1), "rms": round(rms, 1),
            "weights": wts, "n_links": len(links)}


# ----------------------------- directional bias -----------------------------
def azimuth_deg(frm, to):
    d = np.array(to) - np.array(frm)
    return float((np.degrees(np.arctan2(d[1], d[0]))) % 360.0)


def sinusoid_fit(az_deg, resid):
    """Fit resid = c0 + c1 cos(az) + c2 sin(az). Return offset, amp, phase, R2."""
    az = np.radians(np.array(az_deg))
    y = np.array(resid, dtype=float)
    Amat = np.column_stack([np.ones_like(az), np.cos(az), np.sin(az)])
    coef, *_ = np.linalg.lstsq(Amat, y, rcond=None)
    pred = Amat @ coef
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) + 1e-9
    r2 = 1.0 - ss_res / ss_tot
    offset = float(coef[0])
    amp = float(np.hypot(coef[1], coef[2]))
    phase = float(np.degrees(np.arctan2(coef[2], coef[1])) % 360.0)
    return {"offset_mm": round(offset, 1), "amplitude_mm": round(amp, 1),
            "phase_deg": round(phase, 1), "r2": round(r2, 3)}


def directional_analysis(pos6, D, target, clean=CLEAN, flagged=None):
    """Positions: clean core fixed, target + other flagged anchors multilaterated.
    Returns per-link az/resid over ALL available links + sinusoid fit."""
    ml = multilat_vs_clean6(pos6, D, target, clean=clean)
    if ml is None:
        return None
    posmap = dict(pos6)
    posmap[target] = ml["pos"]
    # place the OTHER flagged anchors too so their links can be included
    if flagged is None:
        flagged = [l for l in LABELS if l not in clean]
    for other in flagged:
        if other == target or other in posmap:
            continue
        ml_o = multilat_vs_clean6(pos6, D, other, clean=clean)
        if ml_o is not None:
            posmap[other] = ml_o["pos"]
    rows = []
    for l in LABELS:
        if l == target:
            continue
        mv = undirected_med(D, target, l)
        if mv is None or l not in posmap:
            continue
        pred = float(np.linalg.norm(ml["pos"] - posmap[l]))
        rows.append({"link": f"{target}-{l}", "peer": l,
                     "az_deg": round(azimuth_deg(ml["pos"], posmap[l]), 1),
                     "measured_mm": round(mv, 1), "predicted_mm": round(pred, 1),
                     "resid_mm": round(mv - pred, 1)})
    fit = sinusoid_fit([r["az_deg"] for r in rows], [r["resid_mm"] for r in rows])
    return {"pos": ml["pos"].tolist(), "rms": ml["rms"], "wrms": ml["wrms"],
            "rows": rows, "fit": fit}


# ----------------------------- temporal stability -----------------------------
def temporal_chunks(pos6, D, target, clean=CLEAN, nchunks=4):
    """Chunk the target's OWN round (target->clean) by time index; multilaterate each."""
    per_link_series = {}
    for l in clean:
        s = D.get((target, l))         # target-as-master, time-ordered
        if s:
            per_link_series[l] = s
    if len(per_link_series) < 4:
        return {"available": False,
                "note": f"{target}-as-master round absent/short (promote fail?); temporal N/A"}
    nmin = min(len(v) for v in per_link_series.values())
    if nmin < nchunks * 4:
        return {"available": False, "note": f"too few sets ({nmin}) for {nchunks} chunks"}
    # anchor the chunk solves in the correct basin using the full-round position
    full_links = [(l, med(v)) for l, v in per_link_series.items()]
    seed, _r, _wr, _rm, _w = robust_multistart(pos6, full_links)
    edges = np.linspace(0, nmin, nchunks + 1).astype(int)
    chunk_pos = []
    for c in range(nchunks):
        lo, hi = edges[c], edges[c + 1]
        links = [(l, med(per_link_series[l][lo:hi])) for l in per_link_series]
        x, _resid, wrms, _rms, _w = robust_place(pos6, links, x0=seed)
        chunk_pos.append({"chunk": c + 1, "n": int(hi - lo),
                          "pos_mm": [round(float(v), 1) for v in x],
                          "wrms_mm": round(wrms, 1)})
    P = np.array([cp["pos_mm"] for cp in chunk_pos])
    spread = float(np.max([np.linalg.norm(P[i] - P[j])
                           for i in range(len(P)) for j in range(i + 1, len(P))]))
    axis_std = [round(float(s), 1) for s in P.std(axis=0)]
    return {"available": True, "chunks": chunk_pos,
            "max_pairwise_mm": round(spread, 1), "per_axis_std_mm": axis_std,
            "direction": f"{target}->clean6 (own round)"}


# ----------------------------- pairwise + deltas -----------------------------
def pairwise(D, target):
    out = {}
    for l in LABELS:
        if l == target:
            continue
        v = undirected_vals(D, target, l)
        out[l] = {"median_mm": med(v), "mad_mm": mad(v), "n": len(v),
                  "dir_ba": {"median": undirected_med({k: D[k] for k in D if k == (target, l)}, target, l)
                             if (target, l) in D else None},
                  "median_TX": med(D.get((target, l), [])),   # target as master
                  "median_RX": med(D.get((l, target), [])),   # target as peer
                  "n_TX": len(D.get((target, l), [])),
                  "n_RX": len(D.get((l, target), []))}
    return out


# ----------------------------- per-anchor RMS (Task 6) -----------------------------
def per_anchor_rms(D, clean=CLEAN):
    """For each anchor, multilaterate vs this dataset's clean core; return rms + worst link."""
    pos6, rms6 = clean6_frame(D, clean=clean)
    res = {}
    for a in LABELS:
        clean_a = [c for c in clean if c != a]
        seeds = [pos6[a]] if a in pos6 else None   # core anchors: seed at MDS pos
        ml = multilat_vs_clean6(pos6, D, a, clean=clean_a, seeds=seeds)
        if ml is None:
            res[a] = {"rms_mm": None, "worst_link": None, "worst_resid_mm": None, "n_links": 0}
            continue
        worst = max(ml["resid"].items(), key=lambda kv: abs(kv[1]))
        res[a] = {"rms_mm": ml["rms"], "worst_link": f"{a}-{worst[0]}",
                  "worst_resid_mm": round(worst[1], 1), "n_links": ml["n_links"]}
    return res, round(rms6, 1)
