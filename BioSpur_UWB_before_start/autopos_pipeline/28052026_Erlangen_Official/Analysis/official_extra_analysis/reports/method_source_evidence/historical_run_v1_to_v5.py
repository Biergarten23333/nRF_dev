#!/usr/bin/env python3
from __future__ import annotations

import csv
import itertools
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix


ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT.parents[1]
DATA_ROOT = PIPELINE / "outdoor_v4_20260504"
SWEEP_CSV = DATA_ROOT / "sweeps/inter_anchor_500set_20260504_185011/pairs_all.csv"
CAPTURE_ROOT = DATA_ROOT / "tr_captures"
ID02_DIR = CAPTURE_ROOT / "ID02_static_center_mid_20260504_192643"

ANCHORS = "ABCDEFGH"
LOWER = {0, 1, 2, 3}
UPPER = {4, 5, 6, 7}
TAG_ORDER = ["BS2DCE", "BSDC91", "BSF66F"]


def rms(vals):
    arr = np.asarray(vals, dtype=float)
    return float(np.sqrt(np.mean(arr * arr))) if arr.size else float("nan")


def mad_sigma(vals, floor=1.0):
    vals = np.asarray(list(vals), dtype=float)
    if vals.size == 0:
        return float(floor)
    med = float(np.median(vals))
    mad = float(np.median(np.abs(vals - med)))
    return max(float(floor), 1.4826 * mad)


def anchor_idx(v):
    s = str(v).strip().upper()
    if s in ANCHORS:
        return ANCHORS.index(s)
    return int(s)


def capture_id(path: Path):
    m = re.match(r"ID(\d+)", path.name)
    return int(m.group(1)) if m else -1


def gauge_align(xyz):
    xyz = np.asarray(xyz, dtype=float).copy()
    xyz -= xyz[0]
    bx = xyz[1]
    bn = np.linalg.norm(bx)
    if bn < 1e-9:
        return xyz
    ex = bx / bn
    c = xyz[2]
    c_perp = c - np.dot(c, ex) * ex
    ey = np.array([0.0, 1.0, 0.0]) if np.linalg.norm(c_perp) < 1e-9 else c_perp / np.linalg.norm(c_perp)
    ez = np.cross(ex, ey)
    rot = np.vstack([ex, ey, ez]).T
    out = xyz @ rot
    if np.mean(out[4:, 2]) < np.mean(out[:4, 2]):
        out[:, 2] *= -1
    out[0] = 0
    out[1, 1:] = 0
    out[2, 2] = 0
    return out


def pack_pos(xyz):
    xyz = gauge_align(xyz)
    vals = [xyz[1, 0], xyz[2, 0], xyz[2, 1]]
    vals.extend(xyz[3].tolist())
    for aid in range(4, 8):
        vals.extend(xyz[aid].tolist())
    return np.asarray(vals, dtype=float)


def unpack_pos(x):
    xyz = np.zeros((8, 3), dtype=float)
    k = 0
    xyz[1] = [x[k], 0.0, 0.0]
    k += 1
    xyz[2] = [x[k], x[k + 1], 0.0]
    k += 2
    xyz[3] = x[k:k + 3]
    k += 3
    for aid in range(4, 8):
        xyz[aid] = x[k:k + 3]
        k += 3
    return xyz


POS_PARAM_ANCHOR_DIM = [(1, 0), (2, 0), (2, 1)] + [(3, d) for d in range(3)] + [(aid, d) for aid in range(4, 8) for d in range(3)]


def classical_mds(dist):
    d2 = dist * dist
    j = np.eye(8) - np.ones((8, 8)) / 8
    b = -0.5 * j @ d2 @ j
    vals, vecs = np.linalg.eigh(b)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    xyz = vecs[:, :3] * np.sqrt(np.maximum(vals[:3], 0.0))
    return gauge_align(xyz)


def load_raw_sweep():
    directed = defaultdict(list)
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


def fuse_pairs(directed, method):
    pairs = []
    for i in range(8):
        for j in range(i + 1, 8):
            ab = np.asarray(directed[(i, j)], dtype=float)
            ba = np.asarray(directed[(j, i)], dtype=float)
            allv = np.concatenate([ab, ba])
            if method == "v1":
                dist = float(np.mean(allv))
                sigma = float(np.std(allv, ddof=1))
            elif method == "v2":
                mean_ab, mean_ba = float(np.mean(ab)), float(np.mean(ba))
                var_ab = max(1.0, float(np.var(ab, ddof=1)))
                var_ba = max(1.0, float(np.var(ba, ddof=1)))
                dist = float((var_ba * mean_ab + var_ab * mean_ba) / (var_ab + var_ba))
                sigma = math.sqrt((var_ab * var_ba) / (var_ab + var_ba))
            elif method == "v3":
                med_ab, med_ba = float(np.median(ab)), float(np.median(ba))
                sig_ab, sig_ba = mad_sigma(ab, 1.0), mad_sigma(ba, 1.0)
                dist = float((sig_ba**2 * med_ab + sig_ab**2 * med_ba) / (sig_ab**2 + sig_ba**2))
                sigma = mad_sigma(allv, 5.0)
            else:
                raise ValueError(method)
            pairs.append({"i": i, "j": j, "pair": f"{ANCHORS[i]}-{ANCHORS[j]}", "dist": dist, "sigma": max(1.0, sigma)})
    return pairs


def pair_matrix(pairs):
    dist = np.zeros((8, 8), dtype=float)
    for p in pairs:
        dist[p["i"], p["j"]] = dist[p["j"], p["i"]] = p["dist"]
    return dist


def height_prior_residuals(xyz, lam):
    if lam <= 0:
        return []
    lower_z = xyz[list(LOWER), 2]
    upper_z = xyz[list(UPPER), 2]
    return (math.sqrt(lam) * np.r_[lower_z - np.mean(lower_z), upper_z - np.mean(upper_z)]).tolist()


def solve_no_delay(pairs, init_xyz, structural_prior=False):
    xyz = gauge_align(init_xyz)
    lams = [0.01, 0.005, 0.0025] if structural_prior else [0.0]
    result = None
    for lam in lams:
        def fun(x):
            cur = unpack_pos(x)
            res = [np.linalg.norm(cur[p["i"]] - cur[p["j"]]) - p["dist"] for p in pairs]
            res.extend(height_prior_residuals(cur, lam))
            return np.asarray(res)
        result = least_squares(fun, pack_pos(xyz), loss="linear", max_nfev=1000)
        xyz = gauge_align(unpack_pos(result.x))
    return xyz, np.zeros(8), result


def inter_errors(xyz, delays, pairs):
    rows = []
    for p in pairs:
        pred = float(np.linalg.norm(xyz[p["i"]] - xyz[p["j"]]) + delays[p["i"]] + delays[p["j"]])
        err = pred - p["dist"]
        rows.append({"pair": p["pair"], "meas_mm": p["dist"], "pred_mm": pred, "err_mm": err, "abs_err_mm": abs(err)})
    return sorted(rows, key=lambda r: r["abs_err_mm"], reverse=True)


def layout_stats(xyz, d, pairs):
    errs = [r["err_mm"] for r in inter_errors(xyz, d, pairs)]
    in30 = [e for e in errs if abs(e) <= 30]
    return {
        "inter_rms_all_mm": rms(errs),
        "inter_rms_inlier_30_mm": rms(in30),
        "n_inlier_30": len(in30),
        "delay_range_mm": float(np.max(d) - np.min(d)),
    }


def tukey_weights(resids):
    sigma = max(5.0, mad_sigma(resids, 0.0))
    c_t = 4.685 * sigma
    weights = []
    for r in resids:
        u = float(r) / c_t
        weights.append((1.0 - u * u) ** 2 if abs(u) <= 1.0 else 0.0)
    return np.asarray(weights), sigma, c_t


def solve_v3_full(pairs, init_xyz):
    xyz = gauge_align(init_xyz)
    delays = np.zeros(8)
    log_lines = []
    converged = False
    final_weights = np.ones(len(pairs))
    for it in range(50):
        old_xyz = xyz.copy()
        old_delays = delays.copy()
        raw = np.asarray([np.linalg.norm(xyz[p["i"]] - xyz[p["j"]]) + delays[p["i"]] + delays[p["j"]] - p["dist"] for p in pairs])
        weights, sigma, c_t = tukey_weights(raw)
        final_weights = weights

        def fun(x):
            cur = unpack_pos(x)
            res = []
            for n, p in enumerate(pairs):
                r = np.linalg.norm(cur[p["i"]] - cur[p["j"]]) + delays[p["i"]] + delays[p["j"]] - p["dist"]
                res.append(math.sqrt(max(0.0, weights[n])) * r)
            return np.asarray(res)

        result = least_squares(fun, pack_pos(xyz), loss="linear", max_nfev=1000)
        xyz = gauge_align(unpack_pos(result.x))

        new_delays = delays.copy()
        for i in range(1, 8):
            estimates = []
            for p in pairs:
                if p["i"] == i:
                    other = p["j"]
                elif p["j"] == i:
                    other = p["i"]
                else:
                    continue
                geom = float(np.linalg.norm(xyz[i] - xyz[other]))
                pair_bias = p["dist"] - geom
                estimates.append(pair_bias - delays[other])
            new_delays[i] = float(np.median(estimates))
        new_delays[0] = 0.0
        delays = new_delays

        errs = [r["err_mm"] for r in inter_errors(xyz, delays, pairs)]
        max_shift = float(np.max(np.linalg.norm(xyz - old_xyz, axis=1)))
        max_dshift = float(np.max(np.abs(delays - old_delays)))
        log_lines.append(f"Iter {it}: sigma={sigma:.2f} c_T={c_t:.2f} zero_w={int(np.sum(weights <= 1e-12))} rms={rms(errs):.2f} pos_shift={max_shift:.4f} delay_shift={max_dshift:.4f}")
        log_lines.append("  " + ", ".join(f"{ANCHORS[i]}={delays[i]:+.2f}" for i in range(8)))
        if max_shift < 0.1 and max_dshift < 0.05:
            converged = True
            break
    return xyz, delays, {"success": converged, "iterations": it + 1, "weights": final_weights, "log": "\n".join(log_lines)}


def solve_v4_interonly(pairs, init_xyz):
    def unpack(x):
        xyz = unpack_pos(x[:18])
        d = np.zeros(8)
        d[1:] = x[18:25]
        return xyz, d

    def fun(x):
        xyz, d = unpack(x)
        res = [(np.linalg.norm(xyz[p["i"]] - xyz[p["j"]]) + d[p["i"]] + d[p["j"]] - p["dist"]) / 15.0 for p in pairs]
        res.extend((d[1:] / 20.0).tolist())
        return np.asarray(res)

    x0 = np.r_[pack_pos(init_xyz), np.zeros(7)]
    lo = np.r_[np.full(18, -np.inf), np.full(7, -60.0)]
    hi = np.r_[np.full(18, np.inf), np.full(7, 60.0)]
    result = least_squares(fun, x0, loss="huber", f_scale=30.0 / 15.0, bounds=(lo, hi), max_nfev=2000)
    xyz, d = unpack(result.x)
    return gauge_align(xyz), d, result


def find_tr_all(capture_dir):
    paths = sorted(capture_dir.glob("recv_*/tr_all.csv"))
    return paths[0] if paths else None


def load_tag_ranges(ids, tag_filter=None):
    rows = []
    for cap in sorted(CAPTURE_ROOT.glob("ID*"), key=capture_id):
        cid = capture_id(cap)
        if cid not in ids:
            continue
        tr = find_tr_all(cap)
        if tr is None:
            continue
        with tr.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if int(float(row["valid"])) != 1:
                    continue
                tag = row["peer_name"].strip()
                if tag_filter is not None and tag not in tag_filter:
                    continue
                aid = int(row["anchor_id"])
                rng = float(row["range_mm"])
                if 0 <= aid < 8 and rng > 0:
                    rows.append({"capture": cid, "sweep": int(row["sweep"]), "tag": tag, "anchor": aid, "range": rng})
    return rows


def build_frame_map(tag_rows, subsample=100):
    keys = sorted({(r["capture"], r["tag"], r["sweep"]) for r in tag_rows})
    selected = set(keys[::subsample])
    frame_map = {key: idx for idx, key in enumerate(sorted(selected))}
    kept = [r for r in tag_rows if (r["capture"], r["tag"], r["sweep"]) in frame_map]
    return frame_map, kept


def init_tag_positions(anchor_xyz, tag_rows, frame_map):
    pos = np.zeros((len(frame_map), 3))
    grouped = defaultdict(list)
    for r in tag_rows:
        grouped[(r["capture"], r["tag"], r["sweep"])].append(r)
    for key, idx in frame_map.items():
        obs = grouped[key]
        weights = np.asarray([1.0 / max(100.0, o["range"]) for o in obs])
        pts = np.asarray([anchor_xyz[o["anchor"]] for o in obs])
        pos[idx] = np.average(pts, axis=0, weights=weights)
    return pos


def solve_v4_joint(pairs, tag_rows, tag_delay_free, init_xyz, init_d_anchor, init_tag_delays=None, max_nfev=5000):
    frame_map, kept = build_frame_map(tag_rows, 100)
    tag_free = list(tag_delay_free)
    tag_free_index = {t: n for n, t in enumerate(tag_free)}
    tag_init = init_tag_positions(init_xyz, kept, frame_map)
    if init_tag_delays is None:
        init_tag_delays = {}

    def unpack(x):
        xyz = unpack_pos(x[:18])
        d_anchor = np.zeros(8)
        d_anchor[1:] = x[18:25]
        k = 25
        d_tag = {"BS2DCE": 0.0, "BSDC91": 0.0, "BSF66F": 0.0}
        for tag in tag_free:
            d_tag[tag] = x[k]
            k += 1
        tag_pos = x[k:].reshape((len(frame_map), 3)) if frame_map else np.zeros((0, 3))
        return xyz, d_anchor, d_tag, tag_pos

    def fun(x):
        xyz, d_anchor, d_tag, tag_pos = unpack(x)
        res = [(np.linalg.norm(xyz[p["i"]] - xyz[p["j"]]) + d_anchor[p["i"]] + d_anchor[p["j"]] - p["dist"]) / 15.0 for p in pairs]
        res.extend((d_anchor[1:] / 20.0).tolist())
        for tag in tag_free:
            res.append(d_tag[tag] / 20.0)
        for r in kept:
            fidx = frame_map[(r["capture"], r["tag"], r["sweep"])]
            pred = np.linalg.norm(tag_pos[fidx] - xyz[r["anchor"]]) + d_anchor[r["anchor"]] + d_tag.get(r["tag"], 0.0)
            res.append((pred - r["range"]) / 80.0)
        return np.asarray(res)

    x0 = [*pack_pos(init_xyz), *init_d_anchor[1:]]
    x0.extend([init_tag_delays.get(t, 0.0) for t in tag_free])
    x0.extend(tag_init.reshape(-1).tolist())
    x0 = np.asarray(x0, dtype=float)
    n = len(x0)
    lo = np.full(n, -np.inf)
    hi = np.full(n, np.inf)
    lo[18:25], hi[18:25] = -60.0, 60.0
    td0, td1 = 25, 25 + len(tag_free)
    lo[td0:td1], hi[td0:td1] = -60.0, 60.0

    m = 28 + 7 + len(tag_free) + len(kept)
    sparsity = lil_matrix((m, n), dtype=int)
    row = 0
    for p in pairs:
        for col, (aid, _dim) in enumerate(POS_PARAM_ANCHOR_DIM):
            if aid in (p["i"], p["j"]):
                sparsity[row, col] = 1
        for aid in (p["i"], p["j"]):
            if aid > 0:
                sparsity[row, 18 + aid - 1] = 1
        row += 1
    for aid in range(1, 8):
        sparsity[row, 18 + aid - 1] = 1
        row += 1
    for tag in tag_free:
        sparsity[row, 25 + tag_free_index[tag]] = 1
        row += 1
    tag_base = 25 + len(tag_free)
    for r in kept:
        aid = r["anchor"]
        for col, (pa, _dim) in enumerate(POS_PARAM_ANCHOR_DIM):
            if pa == aid:
                sparsity[row, col] = 1
        if aid > 0:
            sparsity[row, 18 + aid - 1] = 1
        if r["tag"] in tag_free_index:
            sparsity[row, 25 + tag_free_index[r["tag"]]] = 1
        fidx = frame_map[(r["capture"], r["tag"], r["sweep"])]
        for k in range(3):
            sparsity[row, tag_base + 3 * fidx + k] = 1
        row += 1

    result = least_squares(
        fun,
        x0,
        loss="huber",
        f_scale=30.0 / 15.0,
        bounds=(lo, hi),
        jac_sparsity=sparsity.tocsr(),
        max_nfev=max_nfev,
        ftol=1e-4,
        xtol=1e-4,
        gtol=1e-4,
        verbose=0,
    )
    xyz, d_anchor, d_tag, _tag_pos = unpack(result.x)
    stats = {"n_tag_rows_used": len(kept), "n_tag_frames": len(frame_map), "optimizer_success": bool(result.success), "optimizer_status": int(result.status), "optimizer_nfev": int(result.nfev), "optimizer_cost": float(result.cost)}
    return gauge_align(xyz), d_anchor, d_tag, result, stats


def save_layout(path, name, xyz, d_anchor, d_tag, stats):
    data = {
        "name": name,
        "anchors": [{"id": i, "label": ANCHORS[i], "x_mm": float(xyz[i, 0]), "y_mm": float(xyz[i, 1]), "z_mm": float(xyz[i, 2]), "d_anchor_mm": float(d_anchor[i])} for i in range(8)],
        "tags": [{"name": tag, "d_tag_mm": float(val)} for tag, val in sorted(d_tag.items())],
        "stats": stats,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_frames(capture_dir):
    tr = find_tr_all(capture_dir)
    if tr is None:
        return []
    frames = defaultdict(list)
    with tr.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(float(row["valid"])) != 1:
                continue
            aid = int(row["anchor_id"])
            rng = float(row["range_mm"])
            if 0 <= aid < 8 and rng > 0:
                frames[int(row["sweep"])].append((aid, rng))
    return [v for _k, v in sorted(frames.items()) if len(v) >= 4]


def solve_position(obs, xyz, d_anchor, x0=None):
    if x0 is None:
        x0 = np.mean([xyz[a] for a, _r in obs], axis=0)
    def fun(p):
        return np.asarray([np.linalg.norm(p - xyz[a]) + d_anchor[a] - r for a, r in obs])
    res = least_squares(fun, x0, loss="huber", f_scale=50.0, max_nfev=100)
    return res.x


def eval_frames(frames, xyz, d_anchor, allowed=None):
    positions = []
    last = None
    for frame in frames:
        obs = [(a, r) for a, r in frame if allowed is None or a in allowed]
        if len(obs) < 4:
            continue
        pos = solve_position(obs, xyz, d_anchor, last)
        positions.append(pos)
        last = pos
    arr = np.asarray(positions)
    if arr.size == 0:
        return {"N": 0, "X": float("nan"), "Y": float("nan"), "Z": float("nan"), "3D": float("nan")}
    std = np.std(arr, axis=0, ddof=1)
    return {"N": len(arr), "X": float(std[0]), "Y": float(std[1]), "Z": float(std[2]), "3D": float(np.linalg.norm(std))}


def compute_fim_v5(v4_result):
    jac = np.asarray(v4_result.jac)
    fim = jac.T @ jac
    cov = np.linalg.pinv(fim)
    sig = np.sqrt(np.maximum(0.0, np.diag(cov)))
    rows = []
    for aid in range(8):
        sx = sy = sz = sd = 0.0
        for col, (pa, dim) in enumerate(POS_PARAM_ANCHOR_DIM):
            if pa == aid:
                if dim == 0:
                    sx = sig[col]
                elif dim == 1:
                    sy = sig[col]
                else:
                    sz = sig[col]
        if aid > 0:
            sd = sig[18 + aid - 1]
        rows.append({"anchor": ANCHORS[aid], "sigma_x": sx, "sigma_y": sy, "sigma_z": sz, "sigma_3d": float(np.linalg.norm([sx, sy, sz])), "sigma_d": sd})
    return rows, float(np.linalg.cond(fim))


def gdop_at(pos, xyz):
    rows = []
    for a in range(8):
        vec = pos - xyz[a]
        n = np.linalg.norm(vec)
        if n > 1e-9:
            rows.append(vec / n)
    h = np.asarray(rows)
    q = np.linalg.pinv(h.T @ h)
    return float(np.sqrt(np.trace(q)))


def gdop_static_summary(xyz, d_anchor):
    gdops, stds = [], []
    for cap in sorted(CAPTURE_ROOT.glob("ID*"), key=capture_id):
        cid = capture_id(cap)
        if not (1 <= cid <= 27):
            continue
        frames = load_frames(cap)
        st = eval_frames(frames, xyz, d_anchor)
        if st["N"] <= 10:
            continue
        positions = []
        last = None
        for frame in frames:
            if len(frame) < 4:
                continue
            p = solve_position(frame, xyz, d_anchor, last)
            positions.append(p)
            last = p
        mean_pos = np.mean(np.asarray(positions), axis=0)
        gdops.append(gdop_at(mean_pos, xyz))
        stds.append(st["3D"])
    if len(gdops) < 2:
        return float("nan"), float("nan")
    return float(np.corrcoef(gdops, stds)[0, 1]), float(np.nanmedian(np.asarray(stds) / np.asarray(gdops)))


def fmt(v, nd=1):
    if isinstance(v, str):
        return v
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "nan"
    return f"{float(v):.{nd}f}"


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    return "\n".join(lines)


def main():
    directed = load_raw_sweep()
    p_v1 = fuse_pairs(directed, "v1")
    p_v2 = fuse_pairs(directed, "v2")
    p_v3 = fuse_pairs(directed, "v3")

    init_v1 = classical_mds(pair_matrix(p_v1))
    init_v2 = classical_mds(pair_matrix(p_v2))
    init_v3 = classical_mds(pair_matrix(p_v3))

    print("Solving V1", flush=True)
    v1_xyz, v1_d, v1_res = solve_no_delay(p_v1, init_v1, False)
    save_layout(ROOT / "solves/v1_layout.json", "V1", v1_xyz, v1_d, {}, layout_stats(v1_xyz, v1_d, p_v1) | {"converged": bool(v1_res.success)})
    print("Solving V2", flush=True)
    v2_xyz, v2_d, v2_res = solve_no_delay(p_v2, init_v2, True)
    save_layout(ROOT / "solves/v2_layout.json", "V2", v2_xyz, v2_d, {}, layout_stats(v2_xyz, v2_d, p_v2) | {"converged": bool(v2_res.success)})
    print("Solving V3-lite", flush=True)
    v3l_xyz, v3l_d, v3l_res = solve_no_delay(p_v3, init_v3, True)
    save_layout(ROOT / "solves/v3_lite_layout.json", "V3-lite", v3l_xyz, v3l_d, {}, layout_stats(v3l_xyz, v3l_d, p_v3) | {"converged": bool(v3l_res.success)})
    print("Solving V3-full", flush=True)
    v3_xyz, v3_d, v3_dbg = solve_v3_full(p_v3, init_v3)
    save_layout(ROOT / "solves/v3_full_layout.json", "V3-full", v3_xyz, v3_d, {}, layout_stats(v3_xyz, v3_d, p_v3) | {"converged": bool(v3_dbg["success"])})
    print("Solving V4 inter-only", flush=True)
    v4io_xyz, v4io_d, v4io_res = solve_v4_interonly(p_v3, v3l_xyz)
    save_layout(ROOT / "solves/v4_io_layout.json", "V4-io", v4io_xyz, v4io_d, {}, layout_stats(v4io_xyz, v4io_d, p_v3) | {"converged": bool(v4io_res.success)})
    print("Loading roto tag ranges", flush=True)
    roto_rows = load_tag_ranges(set(range(28, 32)), {"BS2DCE", "BSDC91"})
    print(f"Solving V4 joint roto with raw rows={len(roto_rows)}", flush=True)
    v4r_xyz, v4r_d, v4r_dt, v4r_res, v4r_extra = solve_v4_joint(p_v3, roto_rows, ["BSDC91"], v4io_xyz, v4io_d, max_nfev=5000)
    save_layout(ROOT / "solves/v4_roto_layout.json", "V4-roto", v4r_xyz, v4r_d, v4r_dt, layout_stats(v4r_xyz, v4r_d, p_v3) | v4r_extra | {"converged": bool(v4r_res.success)})
    print("Loading all tag ranges", flush=True)
    all_rows = load_tag_ranges(set(range(1, 32)), {"BS2DCE", "BSDC91", "BSF66F"})
    print(f"Solving V4 joint all with raw rows={len(all_rows)}", flush=True)
    v4a_xyz, v4a_d, v4a_dt, v4a_res, v4a_extra = solve_v4_joint(p_v3, all_rows, ["BSDC91", "BSF66F"], v4r_xyz, v4r_d, v4r_dt, max_nfev=5000)

    layouts = [
        ("V1", "simple avg", "No", v1_xyz, v1_d, {}, p_v1, bool(v1_res.success)),
        ("V2", "IVW", "No", v2_xyz, v2_d, {}, p_v2, bool(v2_res.success)),
        ("V3-lite", "MAD+MVUE", "No", v3l_xyz, v3l_d, {}, p_v3, bool(v3l_res.success)),
        ("V3-full", "MAD+MVUE", "Tukey alt", v3_xyz, v3_d, {}, p_v3, bool(v3_dbg["success"])),
        ("V4-io", "MAD+MVUE", "Huber joint", v4io_xyz, v4io_d, {}, p_v3, bool(v4io_res.success)),
        ("V4-roto", "MAD+MVUE", "Huber joint", v4r_xyz, v4r_d, v4r_dt, p_v3, bool(v4r_res.success)),
        ("V4-all", "MAD+MVUE", "Huber joint", v4a_xyz, v4a_d, v4a_dt, p_v3, bool(v4a_res.success)),
    ]

    for name, _fusion, _delay, xyz, d, dt, pairs, conv in layouts:
        stats = layout_stats(xyz, d, pairs)
        if name == "V4-roto":
            stats |= v4r_extra
        if name == "V4-all":
            stats |= v4a_extra
        save_layout(ROOT / f"solves/{name.lower().replace('-', '_')}_layout.json", name, xyz, d, dt, stats | {"converged": conv})

    (ROOT / "reports/v3_full_iteration_log.txt").write_text(v3_dbg["log"], encoding="utf-8")

    frames_id02 = load_frames(ID02_DIR)
    pos_rows = []
    with (ROOT / "positioning/id02_positioning.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["solver", "N8", "X8", "Y8", "Z8", "D8", "N6", "D6"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for name, _fusion, _delay, xyz, d, _dt, _pairs, _conv in layouts:
            st8 = eval_frames(frames_id02, xyz, d)
            st6 = eval_frames(frames_id02, xyz, d, allowed={0, 1, 2, 4, 5, 6})
            row = {"solver": name, "N8": st8["N"], "X8": st8["X"], "Y8": st8["Y"], "Z8": st8["Z"], "D8": st8["3D"], "N6": st6["N"], "D6": st6["3D"]}
            w.writerow(row)
            pos_rows.append(row)

    fim_rows, cond = compute_fim_v5(v4io_res)
    gdop_r, sigma_range = gdop_static_summary(v4io_xyz, v4io_d)
    (ROOT / "reports/v5_fim_v4_interonly.json").write_text(json.dumps({"condition_number": cond, "anchors": fim_rows, "gdop_pearson_r": gdop_r, "sigma_range_estimate": sigma_range}, indent=2), encoding="utf-8")

    cal_rows = []
    for name, fusion, delay, xyz, d, _dt, pairs, conv in layouts:
        st = layout_stats(xyz, d, pairs)
        cal_rows.append([name, fusion, delay, fmt(st["inter_rms_all_mm"]), fmt(st["inter_rms_inlier_30_mm"]), st["n_inlier_30"], fmt(st["delay_range_mm"]), "Yes" if conv else "No"])

    pos_table_rows = []
    base = None
    prog_rows = []
    for row in pos_rows:
        pos_table_rows.append([row["solver"], fmt(row["X8"]), fmt(row["Y8"]), fmt(row["Z8"]), fmt(row["D8"]), fmt(row["D6"])])
        if base is None:
            base = row["D8"]
        changes = {
            "V1": "baseline",
            "V2": "+IVW fusion",
            "V3-lite": "+MAD+MVUE",
            "V3-full": "+delay est",
            "V4-io": "+Huber joint",
            "V4-roto": "+tag ranges (roto)",
            "V4-all": "+tag ranges (all)",
        }
        prog_rows.append([row["solver"], changes[row["solver"]], fmt(row["D8"]), fmt(row["D6"]), "ref" if row["solver"] == "V1" else fmt(row["D8"] - base)])

    v3_debug_rows = [
        ["Iterations to converge", v3_dbg["iterations"]],
        ["Final delay range", fmt(np.max(v3_d) - np.min(v3_d))],
        ["N Tukey-rejected pairs (weight=0)", int(np.sum(v3_dbg["weights"] <= 1e-12))],
        ["Sigma floor used?", "Yes (5mm)"],
        ["Final delays A..H", ", ".join(f"{ANCHORS[i]}={v3_d[i]:+.1f}" for i in range(8))],
    ]
    delay_rows = []
    for i in range(8):
        delay_rows.append([ANCHORS[i], fmt(v3_d[i]), fmt(v4io_d[i]), fmt(v4r_d[i]), fmt(v4a_d[i])])
    fim_table_rows = [[r["anchor"], fmt(r["sigma_x"], 3), fmt(r["sigma_y"], 3), fmt(r["sigma_z"], 3), fmt(r["sigma_3d"], 3), fmt(r["sigma_d"], 3)] for r in fim_rows]

    report = []
    report.append("# AutoPos V1-V5 Complete Solver Progression\n")
    report.append(f"Output directory: `{ROOT}`\n")
    report.append("## Table 1: Calibration Quality\n")
    report.append(table(["Solver", "Fusion", "Delay est.", "Inter RMS (28)", "Inlier RMS (<=30mm)", "N inlier", "Delay range", "Converged"], cal_rows))
    report.append("\n\n## Table 2: ID02 Positioning (Primary Comparison)\n")
    report.append(table(["Solver", "8-anc X", "Y", "Z", "3D", "6-anc (no D/H) 3D"], pos_table_rows))
    report.append("\n\n## Table 3: V3-full Debug\n")
    report.append(table(["Metric", "Value"], v3_debug_rows))
    report.append("\n\n## Table 4: V4 Delay Comparison\n")
    report.append(table(["Anchor", "V3-full delay", "V4-io delay", "V4-roto delay", "V4-all delay"], delay_rows))
    report.append("\n\n## Table 5: V5 Per-Anchor Uncertainty\n")
    report.append(table(["Anchor", "sigma_x", "sigma_y", "sigma_z", "sigma_3D", "sigma_d"], fim_table_rows))
    report.append(f"\n\nV4-interonly FIM condition number: `{cond:.3e}`. GDOP/static-std Pearson r: `{gdop_r:.3f}`. Estimated sigma_range: `{sigma_range:.1f} mm`.\n")
    report.append("\n## Table 6: Progression Summary\n")
    report.append(table(["Step", "What changed", "ID02 3D std (8-anc)", "ID02 3D std (6-anc)", "Delta"], prog_rows))
    report.append("\n\n## Key Findings\n")
    report.append("- The progression is re-derived from raw sweep/TR data in one standalone script; no previous layouts are used as solver inputs.\n")
    report.append("- V3-full uses a 5 mm Tukey sigma floor, so it avoids the previous <1 mm collapse and over-rejection failure mode.\n")
    report.append("- Compare Table 6 to see whether each solver change helps or hurts ID02, and Table 2 to see whether D/H removal is still beneficial.\n")
    (ROOT / "reports/v1_to_v5_report.md").write_text("\n".join(report), encoding="utf-8")

    print("\n" + table(["Solver", "Inter RMS", "ID02 8anc 3D", "ID02 6anc 3D", "Converged"], [[r[0], r[3], next(fmt(p["D8"]) for p in pos_rows if p["solver"] == r[0]), next(fmt(p["D6"]) for p in pos_rows if p["solver"] == r[0]), r[7]] for r in cal_rows]))
    print(f"\nReport: {ROOT / 'reports/v1_to_v5_report.md'}")


if __name__ == "__main__":
    main()
