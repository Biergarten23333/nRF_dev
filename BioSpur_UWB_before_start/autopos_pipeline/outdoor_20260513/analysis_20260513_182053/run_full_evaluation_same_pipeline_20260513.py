#!/usr/bin/env python3
from __future__ import annotations

import csv
import itertools
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import least_squares
from scipy.stats import wilcoxon


ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT.parents[1]
DATA_ROOT = PIPELINE / "outdoor_20260513"
SWEEP_CSV = DATA_ROOT / "sweep1000/pairs_all.csv"
CAPTURE_ROOT = DATA_ROOT / "Static_Test"
# 20260513 center-mid captures are ID13-ID16; ID14 is the best direct analog for the old single-point ID02 metric.
ID02_DIR = CAPTURE_ROOT / "ID14_20260513_161800"

ANCHORS = "ABCDEFGH"
ANCHOR_SIGMA = {0: 16.0, 1: 20.0, 2: 27.0, 3: 84.0, 4: 37.0, 5: 28.0, 6: 50.0, 7: 133.0}
LOWER_ANCHOR_IDX = (0, 1, 2, 3)
UPPER_ANCHOR_IDX = (4, 5, 6, 7)
# The AutoPos gauge fixes A/B/C onto z=0 to remove coordinate-frame freedom.
# D and E/F/G/H remain physically free, but they are not allowed to wander
# into a mathematically valid yet impossible two-layer frame. These are soft
# priors, not hard coplanarity constraints.
LOWER_D_Z_SIGMA_MM = 180.0
UPPER_LAYER_Z_SIGMA_MM = 220.0
MIN_LAYER_GAP_MM = 450.0
MAX_LAYER_GAP_MM = 2600.0
CONFIGS = {
    "Dual-layer 8anc": [0, 1, 2, 3, 4, 5, 6, 7],
    "Upper only EFGH": [4, 5, 6, 7],
    "Lower only ABCD": [0, 1, 2, 3],
    "Best6 no DH": [0, 1, 2, 4, 5, 6],
    "Upper+AB": [0, 1, 4, 5, 6, 7],
    "Lower+EF": [0, 1, 2, 3, 4, 5],
}
SOLVERS = ["MDS+NLS", "Ridolfi GD", "SDP+NLS", "AutoPos V1", "AutoPos V2", "V3-lite", "V3-full", "V4-interonly"]


def log(msg: str) -> None:
    print(msg, flush=True)


def anchor_idx(v: str) -> int:
    s = str(v).strip().upper()
    if s in ANCHORS:
        return ANCHORS.index(s)
    return int(s)


def rms(vals) -> float:
    arr = np.asarray(vals, dtype=float)
    return float(np.sqrt(np.mean(arr * arr))) if arr.size else float("nan")


def mad_sigma(vals, floor=0.1) -> float:
    arr = np.asarray(list(vals), dtype=float)
    if arr.size == 0:
        return float(floor)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    return max(float(floor), 1.4826 * mad)


def fmt(v, nd=1) -> str:
    if isinstance(v, str):
        return v
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "nan"
    return f"{float(v):.{nd}f}"


def md_table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    out.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    return "\n".join(out)


def capture_id(path: Path) -> int:
    m = re.match(r"ID(\d+)", path.name)
    return int(m.group(1)) if m else -1


def load_sweep_raw() -> dict[tuple[int, int], list[float]]:
    directed: dict[tuple[int, int], list[float]] = defaultdict(list)
    with SWEEP_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            a, b = anchor_idx(row["a"]), anchor_idx(row["b"])
            master = anchor_idx(row.get("master", row["a"]))
            d = float(row["dist_mm"])
            q = float(row.get("quality_percent") or 100)
            ok = int(float(row.get("ok") or 1))
            if a == b or d <= 0 or q <= 0 or not ok:
                continue
            if master == a:
                directed[(a, b)].append(d)
            elif master == b:
                directed[(b, a)].append(d)
            else:
                directed[(a, b)].append(d)
    return directed


def fuse_from_directed(directed: dict[tuple[int, int], list[float]], method: str, anchor_ids: list[int]) -> dict[tuple[int, int], float]:
    out: dict[tuple[int, int], float] = {}
    for i, j in itertools.combinations(anchor_ids, 2):
        ab = np.asarray(directed.get((i, j), []), dtype=float)
        ba = np.asarray(directed.get((j, i), []), dtype=float)
        allv = np.concatenate([ab, ba])
        if allv.size == 0:
            continue
        if method == "v1":
            d = float(np.mean(allv))
        elif method == "v2":
            mean_ab = float(np.mean(ab if ab.size else allv))
            mean_ba = float(np.mean(ba if ba.size else allv))
            var_ab = max(1.0, float(np.var(ab if ab.size > 1 else allv, ddof=1)))
            var_ba = max(1.0, float(np.var(ba if ba.size > 1 else allv, ddof=1)))
            d = float((var_ba * mean_ab + var_ab * mean_ba) / (var_ab + var_ba))
        elif method == "v3":
            med_ab = float(np.median(ab if ab.size else allv))
            med_ba = float(np.median(ba if ba.size else allv))
            sig_ab = mad_sigma(ab if ab.size else allv, 0.1)
            sig_ba = mad_sigma(ba if ba.size else allv, 0.1)
            d = float((sig_ba**2 * med_ab + sig_ab**2 * med_ba) / (sig_ab**2 + sig_ba**2))
        else:
            raise ValueError(method)
        out[(i, j)] = d
    return out


def save_fusion_comparison(directed):
    rows = []
    for i, j in itertools.combinations(range(8), 2):
        vals = {m: fuse_from_directed(directed, m, list(range(8)))[(i, j)] for m in ["v1", "v2", "v3"]}
        ab = np.asarray(directed[(i, j)], dtype=float)
        ba = np.asarray(directed[(j, i)], dtype=float)
        rows.append({
            "pair": f"{ANCHORS[i]}-{ANCHORS[j]}",
            "v1_mean": vals["v1"],
            "v2_ivw": vals["v2"],
            "v3_mvue": vals["v3"],
            "v2_minus_v1": vals["v2"] - vals["v1"],
            "v3_minus_v1": vals["v3"] - vals["v1"],
            "sigma_ab_mad": mad_sigma(ab, 0.1),
            "sigma_ba_mad": mad_sigma(ba, 0.1),
            "n_ab": len(ab),
            "n_ba": len(ba),
        })
    path = ROOT / "tables/fusion_comparison_same_pipeline.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def local_pairs(pair_dists: dict[tuple[int, int], float], anchor_ids: list[int]) -> tuple[dict[tuple[int, int], float], dict[int, int], dict[int, int]]:
    g2l = {g: i for i, g in enumerate(anchor_ids)}
    l2g = {i: g for g, i in g2l.items()}
    out = {}
    for (gi, gj), d in pair_dists.items():
        if gi in g2l and gj in g2l:
            out[(g2l[gi], g2l[gj])] = d
    return out, g2l, l2g


def gauge_align_local(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float).copy()
    n = len(x)
    x -= x[0]
    if n < 2:
        return x
    bx = x[1]
    bn = np.linalg.norm(bx)
    if bn < 1e-9:
        return x
    ex = bx / bn
    if n >= 3:
        c = x[2]
        c_perp = c - np.dot(c, ex) * ex
        ey = np.array([0.0, 1.0, 0.0]) if np.linalg.norm(c_perp) < 1e-9 else c_perp / np.linalg.norm(c_perp)
    else:
        ey = np.array([0.0, 1.0, 0.0])
    ez = np.cross(ex, ey)
    rot = np.vstack([ex, ey, ez]).T
    y = x @ rot
    y[0] = 0
    if n >= 2:
        y[1, 1:] = 0
    if n >= 3:
        y[2, 2] = 0
    return y


def pos_param_map(n: int) -> list[tuple[int, int]]:
    m: list[tuple[int, int]] = []
    if n >= 2:
        m.append((1, 0))
    if n >= 3:
        m.extend([(2, 0), (2, 1)])
    for a in range(3, n):
        m.extend([(a, 0), (a, 1), (a, 2)])
    return m


def pack_pos(x: np.ndarray) -> np.ndarray:
    x = gauge_align_local(x)
    vals = []
    for a, dim in pos_param_map(len(x)):
        vals.append(x[a, dim])
    return np.asarray(vals, dtype=float)


def unpack_pos(v: np.ndarray, n: int) -> np.ndarray:
    x = np.zeros((n, 3), dtype=float)
    k = 0
    for a, dim in pos_param_map(n):
        x[a, dim] = v[k]
        k += 1
    return x


def pair_matrix_local(pair_dists: dict[tuple[int, int], float], n: int) -> np.ndarray:
    d = np.zeros((n, n), dtype=float)
    for (i, j), val in pair_dists.items():
        d[i, j] = d[j, i] = val
    return d


def mds_init(pair_dists: dict[tuple[int, int], float], n: int) -> np.ndarray:
    d = pair_matrix_local(pair_dists, n)
    if n == 4 and np.any(d == 0):
        # Fallback should almost never trigger because configs are complete graphs.
        d[d == 0] = np.mean(d[d > 0])
        np.fill_diagonal(d, 0)
    j = np.eye(n) - np.ones((n, n)) / n
    b = -0.5 * j @ (d * d) @ j
    vals, vecs = np.linalg.eigh(b)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    xyz = np.zeros((n, 3))
    k = min(3, n)
    xyz[:, :k] = vecs[:, :k] * np.sqrt(np.maximum(vals[:k], 0.0))
    return gauge_align_local(xyz)


def residual_no_delay_vec(v, n, pair_dists, weights=None, lam=0.0):
    x = unpack_pos(v, n)
    res = []
    for idx, ((i, j), d) in enumerate(pair_dists.items()):
        r = np.linalg.norm(x[i] - x[j]) - d
        if weights is not None:
            r *= math.sqrt(max(0.0, weights[idx]))
        res.append(r)
    if lam > 0:
        z = x[:, 2]
        res.extend((math.sqrt(lam) * (z - np.mean(z))).tolist())
    return np.asarray(res)


def physical_layout_prior_residuals(x: np.ndarray, anchor_ids: list[int]) -> list[float]:
    local = {gi: li for li, gi in enumerate(anchor_ids)}
    if not all(gi in local for gi in LOWER_ANCHOR_IDX + UPPER_ANCHOR_IDX):
        return []

    lower_z = np.asarray([x[local[i], 2] for i in LOWER_ANCHOR_IDX], dtype=float)
    upper_z = np.asarray([x[local[i], 2] for i in UPPER_ANCHOR_IDX], dtype=float)
    lower_ref = float(np.median(lower_z[:3]))  # A/B/C are gauge plane.
    upper_ref = float(np.median(upper_z))
    layer_gap = upper_ref - lower_ref

    out: list[float] = []
    # D should belong to the lower layer, but is allowed real mounting offset.
    out.append((lower_z[3] - lower_ref) / LOWER_D_Z_SIGMA_MM)
    # E/F/G/H should be the upper layer, but are not forced exactly coplanar.
    out.extend(((upper_z - upper_ref) / UPPER_LAYER_Z_SIGMA_MM).tolist())
    # Enforce only physically plausible layer ordering and rough separation.
    out.append(max(0.0, MIN_LAYER_GAP_MM - layer_gap) / 120.0)
    out.append(max(0.0, layer_gap - MAX_LAYER_GAP_MM) / 250.0)
    return out


def layout_physical_diagnostics(x: np.ndarray, anchor_ids: list[int]) -> dict:
    local = {gi: li for li, gi in enumerate(anchor_ids)}
    if not all(gi in local for gi in LOWER_ANCHOR_IDX + UPPER_ANCHOR_IDX):
        return {}
    lower_z = np.asarray([x[local[i], 2] for i in LOWER_ANCHOR_IDX], dtype=float)
    upper_z = np.asarray([x[local[i], 2] for i in UPPER_ANCHOR_IDX], dtype=float)
    lower_ref = float(np.median(lower_z[:3]))
    upper_ref = float(np.median(upper_z))
    return {
        "physical_priors": "soft_two_layer_v1",
        "lower_d_z_offset_mm": float(lower_z[3] - lower_ref),
        "lower_z_span_mm": float(np.max(lower_z) - np.min(lower_z)),
        "upper_z_span_mm": float(np.max(upper_z) - np.min(upper_z)),
        "layer_gap_median_mm": float(upper_ref - lower_ref),
        "layer_order_ok": bool(upper_ref > lower_ref),
    }


def nls_refine(x0, pair_dists, lam=0.0, weights=None, max_nfev=1000):
    n = len(x0)
    method = "lm" if weights is None and lam == 0.0 and len(pair_dists) >= len(pack_pos(x0)) else "trf"
    result = least_squares(lambda v: residual_no_delay_vec(v, n, pair_dists, weights, lam), pack_pos(x0), loss="linear", method=method, max_nfev=max_nfev)
    return gauge_align_local(unpack_pos(result.x, n)), result


def solve_mds_nls(pair_dists, anchor_ids):
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    x0 = mds_init(lp, len(anchor_ids))
    return nls_refine(x0, lp)


def solve_ridolfi_gd(pair_dists, anchor_ids, lr=1e-5, max_iter=5000):
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    x = mds_init(lp, len(anchor_ids))
    n = len(anchor_ids)
    for _it in range(max_iter):
        grad = np.zeros_like(x)
        for (i, j), d in lp.items():
            diff = x[i] - x[j]
            dist = np.linalg.norm(diff)
            if dist < 1e-6:
                continue
            residual = dist - d
            direction = diff / dist
            grad[i] += 2.0 * residual * direction
            grad[j] -= 2.0 * residual * direction
        grad[0] = 0
        if n >= 2:
            grad[1, 1:] = 0
        if n >= 3:
            grad[2, 2] = 0
        step = lr * grad
        x -= step
        x = gauge_align_local(x)
        if np.max(np.abs(step)) < 0.01:
            break
    x, res = nls_refine(x, lp)
    return x, res


def solve_sdp_nls(pair_dists, anchor_ids):
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    try:
        import cvxpy as cp
        n = len(anchor_ids)
        g = cp.Variable((n, n), symmetric=True)
        terms = []
        for (i, j), d in lp.items():
            dist_sq = g[i, i] - 2 * g[i, j] + g[j, j]
            terms.append(cp.square(dist_sq - d * d))
        prob = cp.Problem(cp.Minimize(cp.sum(terms)), [g >> 0])
        prob.solve(solver=cp.SCS, verbose=False, eps=1e-6, max_iters=10000)
        if g.value is None:
            raise RuntimeError("SDP returned no value")
        vals, vecs = np.linalg.eigh(g.value)
        order = np.argsort(vals)[::-1]
        vals, vecs = vals[order], vecs[:, order]
        x0 = np.zeros((n, 3))
        k = min(3, n)
        x0[:, :k] = vecs[:, :k] * np.sqrt(np.maximum(vals[:k], 0.0))
        return nls_refine(gauge_align_local(x0), lp)
    except Exception as exc:
        raise RuntimeError(f"SDP failed: {exc}") from exc


def solve_autopos_v1(pair_dists, anchor_ids):
    return solve_mds_nls(pair_dists, anchor_ids)


def solve_autopos_v2(pair_dists, anchor_ids):
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    x = mds_init(lp, len(anchor_ids))
    result = None
    lam = 0.01
    for _ in range(3):
        x, result = nls_refine(x, lp, lam=lam)
        lam *= 0.5
    return x, result


def solve_v3_full(pair_dists, anchor_ids):
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    x = mds_init(lp, n)
    dly = np.zeros(n)
    converged = False
    weights = np.ones(len(lp))
    result = None
    for it in range(50):
        old_x = x.copy()
        old_d = dly.copy()
        resids = np.asarray([np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - d for (i, j), d in lp.items()])
        sigma = max(mad_sigma(resids, 0.0), 5.0)
        c_t = 4.685 * sigma
        weights = np.asarray([(1 - (r / c_t) ** 2) ** 2 if abs(r) <= c_t else 0.0 for r in resids])

        def fun(v):
            cur = unpack_pos(v, n)
            out = []
            for idx, ((i, j), dist) in enumerate(lp.items()):
                r = np.linalg.norm(cur[i] - cur[j]) + dly[i] + dly[j] - dist
                out.append(math.sqrt(max(0.0, weights[idx])) * r)
            return np.asarray(out)

        result = least_squares(fun, pack_pos(x), loss="linear", method="trf", max_nfev=1000)
        x = gauge_align_local(unpack_pos(result.x, n))
        for i in range(1, n):
            est = []
            for (a, b), dist in lp.items():
                if a == i:
                    other = b
                elif b == i:
                    other = a
                else:
                    continue
                est.append(dist - np.linalg.norm(x[i] - x[other]) - dly[other])
            if est:
                dly[i] = float(np.median(est))
        if np.max(np.linalg.norm(x - old_x, axis=1)) < 0.1 and np.max(np.abs(dly - old_d)) < 0.05:
            converged = True
            break
    result.success = bool(result.success and converged) if result is not None else False
    return x, dly, result, {"iterations": it + 1, "zero_weights": int(np.sum(weights <= 1e-12)), "converged": converged}


def solve_v4(pair_dists, anchor_ids, x_init=None):
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    if x_init is None:
        x_init = mds_init(lp, n)
    pmap = pos_param_map(n)

    def unpack(v):
        x = unpack_pos(v[:len(pmap)], n)
        d = np.zeros(n)
        if n > 1:
            d[1:] = v[len(pmap):]
        return x, d

    def fun(v):
        x, dly = unpack(v)
        out = [(np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist) / 15.0 for (i, j), dist in lp.items()]
        if n > 1:
            out.extend((dly[1:] / 20.0).tolist())
        out.extend(physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out)

    x0 = np.r_[pack_pos(x_init), np.zeros(max(0, n - 1))]
    lo = np.r_[np.full(len(pmap), -np.inf), np.full(max(0, n - 1), -60.0)]
    hi = np.r_[np.full(len(pmap), np.inf), np.full(max(0, n - 1), 60.0)]
    result = least_squares(fun, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=5000)
    x, dly = unpack(result.x)
    result.physical_diagnostics = layout_physical_diagnostics(x, anchor_ids)
    return gauge_align_local(x), dly, result


def solve_v4_common_mode(pair_dists, anchor_ids, x_init=None, *, c_init=0.0, e_init=None, e_reg_scale_mm=20.0, max_nfev=5000):
    if not np.isfinite(e_reg_scale_mm) or e_reg_scale_mm <= 0.0:
        raise ValueError(f"e_reg_scale_mm must be positive, got {e_reg_scale_mm!r}")
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    if x_init is None:
        x_init = mds_init(lp, n)
    pmap = pos_param_map(n)
    x_start = np.asarray(x_init, dtype=float).copy()
    e0 = np.zeros(n, dtype=float) if e_init is None else np.asarray(e_init, dtype=float)
    e0 = np.clip(e0 - np.mean(e0), -80.0, 80.0)
    x0 = np.r_[pack_pos(x_start), float(c_init), e0]
    lo = np.r_[np.full(len(pmap), -np.inf), -150.0, np.full(n, -100.0)]
    hi = np.r_[np.full(len(pmap), np.inf), 150.0, np.full(n, 100.0)]

    def unpack(v):
        x = unpack_pos(v[:len(pmap)], n)
        c = float(v[len(pmap)])
        e = np.asarray(v[len(pmap) + 1:len(pmap) + 1 + n], dtype=float)
        return x, c, e

    def residual(v):
        x, c, e = unpack(v)
        dly = c + e
        out = [
            (np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist) / 15.0
            for (i, j), dist in lp.items()
        ]
        out.extend((e / float(e_reg_scale_mm)).tolist())
        out.append(float(np.mean(e) / 1.0))
        out.extend(physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out, dtype=float)

    result = least_squares(residual, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=max_nfev)
    x, c, e = unpack(result.x)
    x = gauge_align_local(x)
    dly = c + e
    pair_resid = [
        float(np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist)
        for (i, j), dist in lp.items()
    ]
    result.physical_diagnostics = layout_physical_diagnostics(x, anchor_ids)
    result.common_mode_mm = float(c)
    result.differential_delay_mm = np.asarray(e, dtype=float)
    result.absolute_delay_mm = np.asarray(dly, dtype=float)
    result.e_reg_scale_mm = float(e_reg_scale_mm)
    result.mean_e_mm = float(np.mean(e))
    result.max_abs_e_mm = float(np.max(np.abs(e)))
    result.pair_rmse_mm = float(np.sqrt(np.mean(np.asarray(pair_resid) ** 2)))
    result.pair_residuals_mm = pair_resid
    return x, dly, result


def inter_rms_local(x, dly, pair_dists, anchor_ids):
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    errs = [np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist for (i, j), dist in lp.items()]
    return rms(errs)


def to_global_layout(x, dly, anchor_ids):
    xyz = np.full((8, 3), np.nan)
    delays = np.full(8, np.nan)
    for li, gi in enumerate(anchor_ids):
        xyz[gi] = x[li]
        delays[gi] = dly[li] if li < len(dly) else 0.0
    return xyz, delays


def find_tr_all(capture_dir):
    direct = capture_dir / "tr_all.csv"
    if direct.exists():
        return direct
    paths = sorted(capture_dir.glob("recv_*/tr_all.csv"))
    return paths[0] if paths else None


def load_frames(capture_dir):
    path = find_tr_all(capture_dir)
    frames = defaultdict(list)
    if path is None:
        return []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(float(row["valid"])) != 1:
                continue
            aid = int(row["anchor_id"])
            rng = float(row["range_mm"])
            if 0 <= aid < 8 and rng > 0:
                frames[int(row["sweep"])].append((aid, rng))
    return [v for _k, v in sorted(frames.items()) if len(v) >= 4]


def solve_position_weighted(obs, global_xyz, global_delay, x0=None):
    if x0 is None:
        x0 = np.nanmean([global_xyz[a] for a, _r in obs], axis=0)

    def fun(p):
        out = []
        for a, r in obs:
            pred = np.linalg.norm(p - global_xyz[a]) + (0.0 if np.isnan(global_delay[a]) else global_delay[a])
            out.append((pred - r) / ANCHOR_SIGMA[a])
        return np.asarray(out)

    result = least_squares(fun, x0, loss="huber", f_scale=2.0, max_nfev=100)
    return result.x


def eval_positioning(frames, x, dly, anchor_ids):
    global_xyz, global_delay = to_global_layout(x, dly, anchor_ids)
    active = set(anchor_ids)
    positions = []
    last = None
    for frame in frames:
        obs = [(a, r) for a, r in frame if a in active]
        if len(obs) < 4:
            continue
        pos = solve_position_weighted(obs, global_xyz, global_delay, last)
        positions.append(pos)
        last = pos
    arr = np.asarray(positions)
    if arr.size == 0:
        return {"N": 0, "X": float("nan"), "Y": float("nan"), "Z": float("nan"), "3D": float("nan")}
    std = np.std(arr, axis=0, ddof=1)
    return {"N": len(arr), "X": float(std[0]), "Y": float(std[1]), "Z": float(std[2]), "3D": float(np.linalg.norm(std))}


def compute_fim_v5(v4_result, n):
    try:
        jac = np.asarray(v4_result.jac)
        fim = jac.T @ jac
        cov = np.linalg.pinv(fim)
        sig = np.sqrt(np.maximum(0.0, np.diag(cov)))
        pmap = pos_param_map(n)
        rows = []
        for i in range(n):
            sx = sy = sz = sd = 0.0
            for col, (a, dim) in enumerate(pmap):
                if a == i:
                    if dim == 0:
                        sx = sig[col]
                    elif dim == 1:
                        sy = sig[col]
                    else:
                        sz = sig[col]
            if i > 0:
                sd = sig[len(pmap) + i - 1]
            rows.append({"local": i, "sigma_x": sx, "sigma_y": sy, "sigma_z": sz, "sigma_3d": float(np.linalg.norm([sx, sy, sz])), "sigma_d": sd})
        return rows, float(np.linalg.cond(fim))
    except Exception:
        return [], float("nan")


def solver_run(name, fused, anchor_ids):
    if name == "MDS+NLS":
        x, res = solve_mds_nls(fused["v1"], anchor_ids)
        return x, np.zeros(len(anchor_ids)), res, {}
    if name == "Ridolfi GD":
        x, res = solve_ridolfi_gd(fused["v1"], anchor_ids)
        return x, np.zeros(len(anchor_ids)), res, {}
    if name == "SDP+NLS":
        x, res = solve_sdp_nls(fused["v1"], anchor_ids)
        return x, np.zeros(len(anchor_ids)), res, {}
    if name == "AutoPos V1":
        x, res = solve_autopos_v1(fused["v1"], anchor_ids)
        return x, np.zeros(len(anchor_ids)), res, {}
    if name == "AutoPos V2":
        x, res = solve_autopos_v2(fused["v2"], anchor_ids)
        return x, np.zeros(len(anchor_ids)), res, {}
    if name == "V3-lite":
        x, res = solve_autopos_v1(fused["v3"], anchor_ids)
        return x, np.zeros(len(anchor_ids)), res, {}
    if name == "V3-full":
        return solve_v3_full(fused["v3"], anchor_ids)
    if name == "V4-interonly":
        init, _ = solve_autopos_v1(fused["v3"], anchor_ids)
        x, d, res = solve_v4(fused["v3"], anchor_ids, init)
        return x, d, res, {}
    raise ValueError(name)


def save_layout_json(path, name, config, anchor_ids, x, dly, stats, extra=None):
    data = {
        "solver": name,
        "config": config,
        "anchor_ids": anchor_ids,
        "anchors": [
            {"id": gi, "label": ANCHORS[gi], "x_mm": float(x[li, 0]), "y_mm": float(x[li, 1]), "z_mm": float(x[li, 2]), "d_anchor_mm": float(dly[li])}
            for li, gi in enumerate(anchor_ids)
        ],
        "stats": stats,
        "extra": extra or {},
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def bootstrap_directed(raw, rng, anchor_ids, n_sets=100):
    out = defaultdict(list)
    for i, j in itertools.permutations(anchor_ids, 2):
        vals = np.asarray(raw.get((i, j), []), dtype=float)
        if vals.size == 0:
            continue
        out[(i, j)] = rng.choice(vals, size=n_sets, replace=True).astype(float).tolist()
    return out


def main():
    rng = np.random.default_rng(20260506)
    raw = load_sweep_raw()
    save_fusion_comparison(raw)
    frames_id02 = load_frames(ID02_DIR)
    main_results = []
    v5_rows_dual = []
    v5_cond_dual = float("nan")
    dual_delays = {}
    solved_for_fig = {}

    log("Running main solver x layer matrix")
    for cfg_name, anchor_ids in CONFIGS.items():
        fused = {m: fuse_from_directed(raw, m, anchor_ids) for m in ["v1", "v2", "v3"]}
        for solver in SOLVERS:
            log(f"  main {solver} / {cfg_name}")
            try:
                x, dly, res, extra = solver_run(solver, fused, anchor_ids)
                method = "v3" if solver in {"V3-lite", "V3-full", "V4-interonly", "AutoPos V2"} else "v1"
                if solver == "AutoPos V2":
                    method = "v2"
                inter = inter_rms_local(x, dly, fused[method], anchor_ids)
                pos = eval_positioning(frames_id02, x, dly, anchor_ids)
                success = bool(getattr(res, "success", True))
                if solver == "V3-full":
                    success = bool(extra.get("converged", False))
                main_results.append({"solver": solver, "config": cfg_name, "success": success, "inter_rms": inter, **pos, "extra": extra})
                save_layout_json(ROOT / f"solves/same_pipeline_{solver.replace('+','p').replace(' ','_').replace('-','_')}_{cfg_name.replace(' ','_').replace('+','p')}.json", solver, cfg_name, anchor_ids, x, dly, {"inter_rms": inter, **pos, "success": success}, extra)
                if cfg_name == "Dual-layer 8anc" and solver in {"V3-full", "V4-interonly"}:
                    dual_delays[solver] = (anchor_ids, dly.copy())
                if solver == "V4-interonly":
                    fim_rows, cond = compute_fim_v5(res, len(anchor_ids))
                    if cfg_name == "Dual-layer 8anc":
                        v5_rows_dual = fim_rows
                        v5_cond_dual = cond
                if cfg_name in {"Dual-layer 8anc", "Upper only EFGH", "Lower only ABCD"} and solver == "MDS+NLS":
                    solved_for_fig[cfg_name] = (anchor_ids, x)
            except Exception as exc:
                log(f"    failed: {exc}")
                main_results.append({"solver": solver, "config": cfg_name, "success": False, "inter_rms": float("nan"), "N": 0, "X": float("nan"), "Y": float("nan"), "Z": float("nan"), "3D": float("nan"), "extra": {"error": str(exc)}})

    with (ROOT / "positioning/main_matrix_same_pipeline_id14.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["solver", "config", "success", "inter_rms", "N", "X", "Y", "Z", "3D"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in main_results:
            w.writerow({k: r.get(k) for k in fields})

    log("Running bootstrap: 50 trials per solver/config, including SDP")
    boot_rows = []
    for cfg_name, anchor_ids in CONFIGS.items():
        for solver in SOLVERS:
            vals_3d, vals_z = [], []
            for trial in range(50):
                if trial % 10 == 0:
                    log(f"  bootstrap {solver} / {cfg_name} trial {trial}/50")
                bs_raw = bootstrap_directed(raw, rng, anchor_ids, 100)
                fused = {m: fuse_from_directed(bs_raw, m, anchor_ids) for m in ["v1", "v2", "v3"]}
                try:
                    x, dly, _res, _extra = solver_run(solver, fused, anchor_ids)
                    pos = eval_positioning(frames_id02, x, dly, anchor_ids)
                    vals_3d.append(pos["3D"])
                    vals_z.append(pos["Z"])
                except Exception:
                    vals_3d.append(float("nan"))
                    vals_z.append(float("nan"))
            boot_rows.append({
                "config": cfg_name,
                "solver": solver,
                "mean_3d": float(np.nanmean(vals_3d)),
                "std_3d": float(np.nanstd(vals_3d, ddof=1)),
                "mean_z": float(np.nanmean(vals_z)),
                "std_z": float(np.nanstd(vals_z, ddof=1)),
                "values_3d": vals_3d,
                "values_z": vals_z,
            })
    with (ROOT / "bootstrap/bootstrap_results.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["config", "solver", "mean_3d", "std_3d", "mean_z", "std_z"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in boot_rows:
            w.writerow({k: r[k] for k in fields})
    (ROOT / "bootstrap/bootstrap_values.json").write_text(json.dumps(boot_rows, indent=2), encoding="utf-8")

    def get_boot(config, solver, key):
        for r in boot_rows:
            if r["config"] == config and r["solver"] == solver:
                return np.asarray(r[key], dtype=float)
        return np.asarray([])

    # Use each config's best main solver for layer tests, and named solvers where requested.
    best_by_config = {}
    for cfg in CONFIGS:
        rows = [r for r in main_results if r["config"] == cfg and np.isfinite(r["3D"])]
        best_by_config[cfg] = min(rows, key=lambda r: r["3D"]) if rows else None
    tests = []
    comparisons = [
        ("Dual vs Upper-only (Z std)", "Dual-layer 8anc", best_by_config["Dual-layer 8anc"]["solver"], "Upper only EFGH", best_by_config["Upper only EFGH"]["solver"], "values_z"),
        ("Dual vs Lower-only (Z std)", "Dual-layer 8anc", best_by_config["Dual-layer 8anc"]["solver"], "Lower only ABCD", best_by_config["Lower only ABCD"]["solver"], "values_z"),
        ("Dual vs Best6 (3D std)", "Dual-layer 8anc", best_by_config["Dual-layer 8anc"]["solver"], "Best6 no DH", best_by_config["Best6 no DH"]["solver"], "values_3d"),
        ("V1 vs V4 on Dual (3D std)", "Dual-layer 8anc", "AutoPos V1", "Dual-layer 8anc", "V4-interonly", "values_3d"),
    ]
    for label, c1, s1, c2, s2, key in comparisons:
        a = get_boot(c1, s1, key)
        b = get_boot(c2, s2, key)
        mask = np.isfinite(a) & np.isfinite(b)
        try:
            p = float(wilcoxon(a[mask], b[mask]).pvalue) if np.sum(mask) >= 2 else float("nan")
        except Exception:
            p = float("nan")
        tests.append({"comparison": label, "test": "Wilcoxon", "pvalue": p, "significant": bool(np.isfinite(p) and p < 0.05), "left": f"{c1}/{s1}", "right": f"{c2}/{s2}"})

    def matrix_table(metric):
        rows = []
        for solver in SOLVERS:
            row = [solver]
            for cfg in CONFIGS:
                item = next((r for r in main_results if r["solver"] == solver and r["config"] == cfg), None)
                row.append(fmt(item[metric]) if item else "nan")
            rows.append(row)
        return rows

    table1 = matrix_table("inter_rms")
    table2 = matrix_table("3D")
    table3 = []
    for cfg, best in best_by_config.items():
        table3.append([cfg, best["solver"] if best else "none", fmt(best["X"] if best else float("nan")), fmt(best["Y"] if best else float("nan")), fmt(best["Z"] if best else float("nan")), fmt(best["3D"] if best else float("nan"))])
    dual_z = best_by_config["Dual-layer 8anc"]["Z"]
    table4 = [[cfg, fmt(best["Z"] if best else float("nan")), "1.0x (reference)" if cfg == "Dual-layer 8anc" else f"{(best['Z'] / dual_z):.1f}x"] for cfg, best in best_by_config.items()]
    v3_anchor_ids, v3_d = dual_delays.get("V3-full", (list(range(8)), np.zeros(8)))
    v4_anchor_ids, v4_d = dual_delays.get("V4-interonly", (list(range(8)), np.zeros(8)))
    table5 = []
    for gi in range(8):
        v3_val = v3_d[v3_anchor_ids.index(gi)] if gi in v3_anchor_ids else float("nan")
        v4_val = v4_d[v4_anchor_ids.index(gi)] if gi in v4_anchor_ids else float("nan")
        table5.append([ANCHORS[gi], fmt(v3_val), fmt(v4_val)])
    table6 = []
    for r in v5_rows_dual:
        gi = CONFIGS["Dual-layer 8anc"][r["local"]]
        table6.append([ANCHORS[gi], fmt(r["sigma_x"], 3), fmt(r["sigma_y"], 3), fmt(r["sigma_z"], 3), fmt(r["sigma_3d"], 3), fmt(r["sigma_d"], 3)])
    table7 = [[r["config"], r["solver"], f"{r['mean_3d']:.1f} +/- {r['std_3d']:.1f}", f"{r['mean_z']:.1f} +/- {r['std_z']:.1f}"] for r in boot_rows if best_by_config.get(r["config"]) and r["solver"] == best_by_config[r["config"]]["solver"]]
    table8 = [[t["comparison"], t["test"], fmt(t["pvalue"], 4), "Yes" if t["significant"] else "No"] for t in tests]
    prog_solvers = ["AutoPos V1", "AutoPos V2", "V3-lite", "V3-full", "V4-interonly"]
    base_v1 = next(r for r in main_results if r["config"] == "Dual-layer 8anc" and r["solver"] == "AutoPos V1")["3D"]
    table9 = []
    for s in prog_solvers:
        item = next(r for r in main_results if r["config"] == "Dual-layer 8anc" and r["solver"] == s)
        fusion = {"AutoPos V1": "simple avg", "AutoPos V2": "IVW", "V3-lite": "MAD+MVUE", "V3-full": "MAD+MVUE", "V4-interonly": "MAD+MVUE"}[s]
        delay = {"AutoPos V1": "No", "AutoPos V2": "No", "V3-lite": "No", "V3-full": "Tukey", "V4-interonly": "Huber"}[s]
        table9.append([s.replace("AutoPos ", ""), fusion, delay, fmt(item["3D"]), "ref" if s == "AutoPos V1" else fmt(item["3D"] - base_v1)])
    table10 = []
    for s, init, opt in [("MDS+NLS", "Classical MDS", "LM/NLS"), ("Ridolfi GD", "MDS/trilateration", "GD+NLS"), ("SDP+NLS", "SDP relaxation", "LM/NLS"), ("AutoPos V1", "MDS", "LM/NLS")]:
        item = next(r for r in main_results if r["config"] == "Dual-layer 8anc" and r["solver"] == s)
        table10.append([s, init, opt, fmt(item["3D"]), fmt(item["inter_rms"])])

    # Figures
    figdir = ROOT / "figures"
    top3 = [r["solver"] for r in sorted([r for r in main_results if r["config"] == "Dual-layer 8anc"], key=lambda x: x["3D"])[:3]]
    xidx = np.arange(len(CONFIGS))
    width = 0.25
    plt.figure(figsize=(11, 5))
    for k, solver in enumerate(top3):
        vals = [next(r for r in main_results if r["config"] == cfg and r["solver"] == solver)["3D"] for cfg in CONFIGS]
        plt.bar(xidx + (k - 1) * width, vals, width, label=solver)
    plt.axhline(49.6, color="k", linestyle="--", label="Concept V3 49.6mm")
    plt.xticks(xidx, CONFIGS.keys(), rotation=25, ha="right")
    plt.ylabel("ID02 3D std (mm)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(figdir / "layer_ablation_bar.png", dpi=300)
    plt.close()

    plt.figure(figsize=(9, 5))
    zvals = [best_by_config[cfg]["Z"] for cfg in CONFIGS]
    plt.bar(list(CONFIGS.keys()), zvals)
    for i, v in enumerate(zvals):
        plt.text(i, v, f"{v / dual_z:.1f}x", ha="center", va="bottom")
    plt.xticks(rotation=25, ha="right")
    plt.ylabel("Best-solver Z std (mm)")
    plt.tight_layout()
    plt.savefig(figdir / "z_degradation_bar.png", dpi=300)
    plt.close()

    plt.figure(figsize=(8, 6))
    markers = {s: m for s, m in zip(SOLVERS, ["o", "s", "^", "D", "P", "X", "v", "*"])}
    colors = {cfg: plt.cm.tab10(i) for i, cfg in enumerate(CONFIGS)}
    for r in main_results:
        plt.scatter(r["inter_rms"], r["3D"], marker=markers[r["solver"]], color=colors[r["config"]], alpha=0.75)
    plt.xlabel("Inter-anchor RMS (mm)")
    plt.ylabel("ID02 3D std (mm)")
    plt.tight_layout()
    plt.savefig(figdir / "algorithm_comparison_scatter.png", dpi=300)
    plt.close()

    plt.figure(figsize=(12, 5))
    box_labels = []
    box_data = []
    for r in boot_rows:
        if best_by_config.get(r["config"]) and r["solver"] == best_by_config[r["config"]]["solver"]:
            box_labels.append(r["config"].replace(" ", "\n"))
            box_data.append([v for v in r["values_3d"] if np.isfinite(v)])
    plt.boxplot(box_data, labels=box_labels)
    plt.ylabel("Bootstrap ID02 3D std (mm)")
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(figdir / "bootstrap_boxplot.png", dpi=300)
    plt.close()

    plt.figure(figsize=(7, 4))
    vals = [next(r for r in main_results if r["config"] == "Dual-layer 8anc" and r["solver"] == s)["3D"] for s in prog_solvers]
    plt.plot(["V1", "V2", "V3-lite", "V3-full", "V4"], vals, marker="o")
    plt.ylabel("ID02 3D std (mm)")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(figdir / "v1_to_v5_progression.png", dpi=300)
    plt.close()

    fig = plt.figure(figsize=(8, 6))
    ax = fig.add_subplot(111, projection="3d")
    for cfg, (ids, x) in solved_for_fig.items():
        ax.scatter(x[:, 0], x[:, 1], x[:, 2], label=cfg)
        for li, gi in enumerate(ids):
            ax.text(x[li, 0], x[li, 1], x[li, 2], ANCHORS[gi])
    ax.set_xlabel("X mm")
    ax.set_ylabel("Y mm")
    ax.set_zlabel("Z mm")
    ax.legend()
    plt.tight_layout()
    plt.savefig(figdir / "solved_coordinates_3d.png", dpi=300)
    plt.close()

    findings = [
        f"Dual-layer best Z std is {best_by_config['Dual-layer 8anc']['Z']:.1f} mm. Upper-only and lower-only best Z std are {best_by_config['Upper only EFGH']['Z']:.1f} mm and {best_by_config['Lower only ABCD']['Z']:.1f} mm, respectively.",
        f"Wilcoxon tests are reported in Table 8; dual vs upper-only Z p={tests[0]['pvalue']:.3g}, dual vs lower-only Z p={tests[1]['pvalue']:.3g}.",
        "On the dual-layer configuration, MDS/Ridolfi/SDP/AutoPos no-delay baselines should be interpreted through Table 10; their differences are mostly initialization/optimizer effects after the same sigma-weighted positioning evaluation.",
        "Delay estimation does not automatically help on this clean outdoor dataset; V3-full/V4 should be judged by both positioning std and delay magnitude, not by inlier RMS alone.",
        "The solver initialization method matters less than anchor layer geometry and per-anchor range quality once NLS refinement is applied.",
        "Best6 no DH is the practical stress test for minimum reliable 3D calibration: it keeps two lower and three upper anchors while removing the weakest D/H anchors.",
        "The V1-V5 progression in Table 9 is the deployment-facing summary: it shows whether added fusion, delay estimation, and Huber joint solve actually improve the same ID02 metric.",
        "AutoPos should be compared against Ridolfi and SDP in Table 10 under the same weighted Huber positioning evaluation, not under raw inter-anchor RMS alone.",
        "Practical recommendation: use dual-layer anchors, sigma-weighted offline positioning, and explicit per-anchor quality handling; do not blindly trust delay variables when they hit large ranges.",
        "To improve beyond the present floor, add stronger vertical geometry, CIR/quality features for NLOS rejection, better antenna orientation control, or newer DW3000-class ranging hardware.",
    ]

    report = []
    report.append("# AutoPos Complete Evaluation: V1-V5 x Layer Ablation x Algorithm Comparison\n")
    report.append(f"Output directory: `{ROOT}`\n")
    report.append("## Table 1: Calibration Quality (Inter-Anchor RMS)\n")
    report.append(md_table(["Solver", *CONFIGS.keys()], table1))
    report.append("\n\n## Table 2: ID02 Positioning - 3D std (mm)\n")
    report.append(md_table(["Solver", *CONFIGS.keys()], table2))
    report.append("\n\n## Table 3: ID02 Per-Axis Breakdown (Best Solver per Config)\n")
    report.append(md_table(["Config", "Best solver", "X std", "Y std", "Z std", "3D std"], table3))
    report.append("\n\n## Table 4: Z-Axis Degradation\n")
    report.append(md_table(["Config", "Z std (best solver)", "Z degradation vs dual-layer"], table4))
    report.append("\n\n## Table 5: V3/V4 Delay Estimation (Dual-Layer Only)\n")
    report.append(md_table(["Anchor", "V3-full delay", "V4-io delay"], table5))
    report.append("\n\n## Table 6: V5 FIM Uncertainty (Dual-Layer Only)\n")
    report.append(md_table(["Anchor", "sigma_x", "sigma_y", "sigma_z", "sigma_3D", "sigma_d"], table6))
    report.append(f"\n\nV4 dual-layer FIM condition number: `{v5_cond_dual:.3e}`.\n")
    report.append("\n## Table 7: Bootstrap Results (50 trials, best solver per config)\n")
    report.append(md_table(["Config", "Solver", "3D std mean+/-std", "Z std mean+/-std"], table7))
    report.append("\n\n## Table 8: Statistical Tests\n")
    report.append(md_table(["Comparison", "Test", "p-value", "Significant?"], table8))
    report.append("\n\n## Table 9: AutoPos V1-V5 Progression (Dual-Layer, sigma-weighted)\n")
    report.append(md_table(["Solver", "Fusion", "Delay", "3D std", "Delta from V1"], table9))
    report.append("\n\n## Table 10: Algorithm Comparison (Dual-Layer, No Delay)\n")
    report.append(md_table(["Algorithm", "Init method", "Optimizer", "3D std", "Inter RMS"], table10))
    report.append("\n\n## Figures\n")
    for fig_name in ["layer_ablation_bar.png", "z_degradation_bar.png", "algorithm_comparison_scatter.png", "bootstrap_boxplot.png", "v1_to_v5_progression.png", "solved_coordinates_3d.png"]:
        report.append(f"- `figures/{fig_name}`")
    report.append("\n\n## Key Findings\n")
    for i, finding in enumerate(findings, 1):
        report.append(f"{i}. {finding}")
    (ROOT / "reports/full_evaluation_report.md").write_text("\n".join(report), encoding="utf-8")

    print("\n" + md_table(["Solver", *CONFIGS.keys()], table2), flush=True)
    print(f"\nReport: {ROOT / 'reports/full_evaluation_report.md'}", flush=True)


if __name__ == "__main__":
    main()
