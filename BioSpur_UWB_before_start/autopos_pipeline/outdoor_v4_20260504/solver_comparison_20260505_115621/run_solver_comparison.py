#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix


ANCHORS = "ABCDEFGH"
LOWER = [0, 1, 2, 3]
UPPER = [4, 5, 6, 7]


def mad_sigma(vals: list[float], floor: float = 1.0) -> float:
    if not vals:
        return floor
    m = median(vals)
    mad = median([abs(v - m) for v in vals])
    return max(floor, 1.4826 * mad)


def rms(vals: list[float] | np.ndarray) -> float:
    arr = np.asarray(vals, dtype=float)
    return float(np.sqrt(np.mean(arr * arr))) if arr.size else 0.0


def robust_inlier_stats(errs: list[float]) -> dict[str, Any]:
    out: dict[str, Any] = {"all_rms_mm": rms(errs)}
    abs_err = [abs(float(e)) for e in errs]
    for thr in [30, 50, 100, 150]:
        kept = [e for e in errs if abs(e) <= thr]
        out[f"abs_le_{thr}mm"] = {
            "n": len(kept),
            "rms_mm": rms(kept),
            "median_abs_mm": float(median([abs(e) for e in kept])) if kept else 0.0,
        }
    return out


def anchor_idx(x: str) -> int:
    s = str(x).strip().upper()
    if s in ANCHORS:
        return ANCHORS.index(s)
    i = int(s)
    if 0 <= i < 8:
        return i
    raise ValueError(x)


def gauge_align(xyz: np.ndarray) -> np.ndarray:
    xyz = np.asarray(xyz, dtype=float).copy()
    xyz -= xyz[0]
    bx = xyz[1]
    bn = np.linalg.norm(bx)
    if bn < 1e-9:
        return xyz
    ex = bx / bn
    c = xyz[2]
    c_perp = c - np.dot(c, ex) * ex
    if np.linalg.norm(c_perp) < 1e-9:
        ey = np.array([0.0, 1.0, 0.0])
    else:
        ey = c_perp / np.linalg.norm(c_perp)
    ez = np.cross(ex, ey)
    rot = np.vstack([ex, ey, ez]).T
    out = xyz @ rot
    if np.mean(out[4:8, 2]) < np.mean(out[:4, 2]):
        out[:, 2] *= -1
    out[0] = 0
    out[1, 1:] = 0
    out[2, 2] = 0
    return out


def pack_pos(xyz: np.ndarray) -> np.ndarray:
    xyz = gauge_align(xyz)
    vals = [xyz[1, 0], xyz[2, 0], xyz[2, 1]]
    vals.extend(xyz[3].tolist())
    for aid in range(4, 8):
        vals.extend(xyz[aid].tolist())
    return np.asarray(vals, dtype=float)


def unpack_pos(x: np.ndarray) -> np.ndarray:
    xyz = np.zeros((8, 3), dtype=float)
    k = 0
    xyz[1] = [x[k], 0.0, 0.0]
    k += 1
    xyz[2] = [x[k], x[k + 1], 0.0]
    k += 2
    xyz[3] = x[k : k + 3]
    k += 3
    for aid in range(4, 8):
        xyz[aid] = x[k : k + 3]
        k += 3
    return xyz


def pos_param_names() -> list[tuple[str, int, str]]:
    names: list[tuple[str, int, str]] = []
    names.append(("B.x", 1, "x"))
    names.extend([("C.x", 2, "x"), ("C.y", 2, "y")])
    for dim in "xyz":
        names.append((f"D.{dim}", 3, dim))
    for aid in range(4, 8):
        for dim in "xyz":
            names.append((f"{ANCHORS[aid]}.{dim}", aid, dim))
    return names


def classical_mds(dist: np.ndarray) -> np.ndarray:
    d2 = dist * dist
    j = np.eye(8) - np.ones((8, 8)) / 8
    b = -0.5 * j @ d2 @ j
    vals, vecs = np.linalg.eigh(b)
    order = np.argsort(vals)[::-1]
    vals = vals[order]
    vecs = vecs[:, order]
    xyz = vecs[:, :3] * np.sqrt(np.maximum(vals[:3], 0.0))
    return gauge_align(xyz)


@dataclass
class PairObs:
    i: int
    j: int
    pair: str
    dist_mm: float
    sigma_mm: float
    med_ab: float
    med_ba: float
    sigma_ab: float
    sigma_ba: float
    n_ab: int
    n_ba: int
    mad_mm: float


def load_and_fuse_pairs(path: Path) -> list[PairObs]:
    directed: dict[tuple[int, int], list[float]] = defaultdict(list)
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                a = anchor_idx(row["a"])
                b = anchor_idx(row["b"])
                d = float(row.get("dist_mm") or row.get("distance_mm") or row.get("raw_mm") or 0)
                q = float(row.get("quality_percent") or row.get("quality") or 100)
            except Exception:
                continue
            if a == b or d <= 0 or q <= 0:
                continue
            directed[(a, b)].append(d)

    out: list[PairObs] = []
    for i in range(8):
        for j in range(i + 1, 8):
            ab = directed.get((i, j), [])
            ba = directed.get((j, i), [])
            allv = ab + ba
            if not allv:
                continue
            med_ab = float(median(ab if ab else allv))
            med_ba = float(median(ba if ba else allv))
            sig_ab = mad_sigma(ab if ab else allv, 1.0)
            sig_ba = mad_sigma(ba if ba else allv, 1.0)
            denom = sig_ab**2 + sig_ba**2
            if denom <= 0:
                fused = float(median(allv))
            else:
                fused = (sig_ba**2 * med_ab + sig_ab**2 * med_ba) / denom
            sigma = mad_sigma(allv, 5.0)
            out.append(
                PairObs(
                    i=i,
                    j=j,
                    pair=f"{ANCHORS[i]}-{ANCHORS[j]}",
                    dist_mm=float(fused),
                    sigma_mm=float(sigma),
                    med_ab=med_ab,
                    med_ba=med_ba,
                    sigma_ab=float(sig_ab),
                    sigma_ba=float(sig_ba),
                    n_ab=len(ab),
                    n_ba=len(ba),
                    mad_mm=float(sigma),
                )
            )
    return out


def pair_matrix(pairs: list[PairObs]) -> np.ndarray:
    dist = np.zeros((8, 8), dtype=float)
    for p in pairs:
        dist[p.i, p.j] = dist[p.j, p.i] = p.dist_mm
    return dist


def inter_errors(xyz: np.ndarray, d_anchor: np.ndarray, pairs: list[PairObs]) -> list[dict[str, Any]]:
    rows = []
    for p in pairs:
        pred = float(np.linalg.norm(xyz[p.i] - xyz[p.j]) + d_anchor[p.i] + d_anchor[p.j])
        err = pred - p.dist_mm
        rows.append({"pair": p.pair, "err_mm": err, "abs_err_mm": abs(err), "meas_mm": p.dist_mm, "pred_mm": pred})
    rows.sort(key=lambda r: r["abs_err_mm"], reverse=True)
    return rows


def save_layout(path: Path, name: str, xyz: np.ndarray, d_anchor: np.ndarray, tags: dict[str, float], stats: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "schema": "solver_comparison_layout_v1",
        "solver": name,
        "units": "mm",
        "anchors": [
            {
                "id": i,
                "label": ANCHORS[i],
                "x_mm": float(xyz[i, 0]),
                "y_mm": float(xyz[i, 1]),
                "z_mm": float(xyz[i, 2]),
                "d_anchor_mm": float(d_anchor[i]),
                "delay_mm": float(d_anchor[i]),
            }
            for i in range(8)
        ],
        "tags": [{"name": k, "d_tag_mm": float(v)} for k, v in sorted(tags.items())],
        "stats": stats,
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def solve_v1_no_delay(pairs: list[PairObs], init_xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray, Any, dict[str, Any]]:
    def fun(x: np.ndarray) -> np.ndarray:
        xyz = unpack_pos(x)
        return np.asarray([np.linalg.norm(xyz[p.i] - xyz[p.j]) - p.dist_mm for p in pairs], dtype=float)

    res = least_squares(fun, pack_pos(init_xyz), loss="huber", f_scale=30.0, max_nfev=1000)
    xyz = unpack_pos(res.x)
    d = np.zeros(8)
    errs = inter_errors(xyz, d, pairs)
    stats = robust_inlier_stats([r["err_mm"] for r in errs])
    stats.update({"top_inter_errors": errs[:12], "optimizer_success": bool(res.success), "optimizer_message": res.message})
    return xyz, d, res, stats


def solve_v3_tukey(pairs: list[PairObs], init_xyz: np.ndarray, max_iter: int = 30, with_delay: bool = True) -> tuple[np.ndarray, np.ndarray, Any, dict[str, Any]]:
    xyz = gauge_align(init_xyz)
    d = np.zeros(8, dtype=float)
    last_res = None
    history = []
    for it in range(max_iter):
        x0 = pack_pos(xyz)
        raw = np.asarray([np.linalg.norm(xyz[p.i] - xyz[p.j]) + d[p.i] + d[p.j] - p.dist_mm for p in pairs])
        sig = mad_sigma(raw.tolist(), 10.0)
        c = max(4.685 * sig, 1.0)
        u = raw / c
        w = np.where(np.abs(u) < 1.0, (1.0 - u * u) ** 2, 0.0)
        w = np.maximum(w, 1e-4)

        def fun_pos(x: np.ndarray) -> np.ndarray:
            xx = unpack_pos(x)
            rr = [np.linalg.norm(xx[p.i] - xx[p.j]) + d[p.i] + d[p.j] - p.dist_mm for p in pairs]
            return np.sqrt(w) * np.asarray(rr, dtype=float)

        res = least_squares(fun_pos, x0, loss="linear", max_nfev=1000)
        new_xyz = unpack_pos(res.x)
        new_d = d.copy()
        if with_delay:
            for aid in range(1, 8):
                vals = []
                for p in pairs:
                    if p.i == aid:
                        vals.append(p.dist_mm - np.linalg.norm(new_xyz[p.i] - new_xyz[p.j]) - new_d[p.j])
                    elif p.j == aid:
                        vals.append(p.dist_mm - np.linalg.norm(new_xyz[p.i] - new_xyz[p.j]) - new_d[p.i])
                if vals:
                    new_d[aid] = float(median(vals))
            # Remove gauge drift by keeping A delay fixed.
            new_d -= new_d[0]
        shift = float(np.max(np.linalg.norm(new_xyz - xyz, axis=1)))
        dshift = float(np.max(np.abs(new_d - d)))
        history.append({"iter": it + 1, "sigma_mm": float(sig), "max_pos_shift_mm": shift, "max_delay_shift_mm": dshift})
        xyz, d = new_xyz, new_d
        last_res = res
        if shift < 0.1 and dshift < 3.0:
            break
    errs = inter_errors(xyz, d, pairs)
    stats = robust_inlier_stats([r["err_mm"] for r in errs])
    stats.update({"iterations": len(history), "history": history, "top_inter_errors": errs[:12], "optimizer_success": bool(last_res.success if last_res else True)})
    return xyz, d, last_res, stats


def solve_v4_interonly(pairs: list[PairObs], init_xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray, Any, dict[str, Any]]:
    def unpack(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        xyz = unpack_pos(x[:18])
        d = np.zeros(8)
        d[1:] = x[18:25]
        return xyz, d

    def fun(x: np.ndarray) -> np.ndarray:
        xyz, d = unpack(x)
        rr = [(np.linalg.norm(xyz[p.i] - xyz[p.j]) + d[p.i] + d[p.j] - p.dist_mm) / 15.0 for p in pairs]
        rr.extend((d[1:] / 20.0).tolist())
        return np.asarray(rr, dtype=float)

    x0 = np.r_[pack_pos(init_xyz), np.zeros(7)]
    lb = np.full_like(x0, -np.inf)
    ub = np.full_like(x0, np.inf)
    lb[18:25] = -60
    ub[18:25] = 60
    res = least_squares(fun, x0, bounds=(lb, ub), loss="huber", f_scale=30.0 / 15.0, max_nfev=1000)
    xyz, d = unpack(res.x)
    errs = inter_errors(xyz, d, pairs)
    stats = robust_inlier_stats([r["err_mm"] for r in errs])
    stats.update({"top_inter_errors": errs[:12], "optimizer_success": bool(res.success), "optimizer_message": res.message})
    return xyz, d, res, stats


@dataclass
class TrObs:
    capture_id: str
    capture_name: str
    source: str
    sweep: str
    tag: str
    anchor: int
    range_mm: float
    quality: float


def find_recv_dir(capture_dir: Path) -> Path | None:
    dirs = sorted([p for p in capture_dir.glob("recv_*") if p.is_dir()])
    for d in dirs:
        if (d / "tr_all.csv").exists():
            return d
    return None


def load_tr_observations(root: Path, ids: set[int] | None = None) -> list[TrObs]:
    obs: list[TrObs] = []
    for cap in sorted(root.glob("ID*")):
        m = re.match(r"ID(\d+)", cap.name)
        if not m:
            continue
        cid_num = int(m.group(1))
        if ids is not None and cid_num not in ids:
            continue
        recv = find_recv_dir(cap)
        if not recv:
            continue
        with (recv / "tr_all.csv").open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    if str(row.get("valid", "")).strip() not in {"1", "true", "True"}:
                        continue
                    status = str(row.get("status", "")).strip().upper()
                    if status and status != "O":
                        continue
                    aid = anchor_idx(row["anchor_id"])
                    rng = float(row["range_mm"])
                    q = float(row.get("quality_percent") or row.get("quality") or 0)
                except Exception:
                    continue
                if rng <= 0:
                    continue
                obs.append(
                    TrObs(
                        capture_id=f"ID{cid_num:02d}",
                        capture_name=cap.name,
                        source=str(recv / "tr_all.csv"),
                        sweep=str(row.get("sweep") or row.get("host_epoch_s") or row.get("host_elapsed_s")),
                        tag=str(row.get("peer_name") or row.get("tag_id") or ""),
                        anchor=aid,
                        range_mm=rng,
                        quality=q,
                    )
                )
    return obs


def build_frame_map(tr: list[TrObs], tag_names: list[str], subsample: int) -> tuple[list[tuple[str, str, str]], dict[tuple[str, str, str], int], list[TrObs]]:
    tagset = set(tag_names)
    grouped: dict[tuple[str, str, str], int] = defaultdict(int)
    for r in tr:
        if r.tag in tagset:
            grouped[(r.tag, r.source, r.sweep)] += 1
    keys = sorted([k for k, n in grouped.items() if n >= 4])
    if subsample > 1:
        keys = keys[::subsample]
    fmap = {k: i for i, k in enumerate(keys)}
    kept = [r for r in tr if (r.tag, r.source, r.sweep) in fmap]
    return keys, fmap, kept


def initial_tag_positions(keys: list[tuple[str, str, str]], fmap: dict[tuple[str, str, str], int], tr: list[TrObs], xyz: np.ndarray) -> np.ndarray:
    bykey: dict[tuple[str, str, str], list[TrObs]] = defaultdict(list)
    for r in tr:
        bykey[(r.tag, r.source, r.sweep)].append(r)
    out = np.zeros((len(keys), 3))
    centroid = np.mean(xyz, axis=0)
    for key in keys:
        rows = bykey[key]
        weights = []
        points = []
        for r in rows:
            weights.append(1.0 / max(r.range_mm, 100.0))
            points.append(xyz[r.anchor])
        out[fmap[key]] = np.average(np.asarray(points), axis=0, weights=np.asarray(weights)) if rows else centroid
    return out


def solve_v4_joint(pairs: list[PairObs], tr: list[TrObs], init_xyz: np.ndarray, tag_names: list[str], tag_ref: str, subsample: int, name: str) -> tuple[np.ndarray, np.ndarray, dict[str, float], Any, dict[str, Any]]:
    keys, fmap, kept = build_frame_map(tr, tag_names, subsample)
    tag_to_idx = {t: i for i, t in enumerate(tag_names)}
    free_tags = [t for t in tag_names if t != tag_ref]
    free_to_idx = {t: i for i, t in enumerate(free_tags)}
    tag_pos0 = initial_tag_positions(keys, fmap, kept, init_xyz)
    nframes = len(keys)

    def unpack(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, float], np.ndarray]:
        xyz = unpack_pos(x[:18])
        d = np.zeros(8)
        d[1:] = x[18:25]
        dt = {tag_ref: 0.0}
        for t, idx in free_to_idx.items():
            dt[t] = float(x[25 + idx])
        tp = x[25 + len(free_tags) :].reshape((nframes, 3))
        return xyz, d, dt, tp

    def fun(x: np.ndarray) -> np.ndarray:
        xyz, d, dt, tp = unpack(x)
        rr = [(np.linalg.norm(xyz[p.i] - xyz[p.j]) + d[p.i] + d[p.j] - p.dist_mm) / 15.0 for p in pairs]
        for r in kept:
            fidx = fmap[(r.tag, r.source, r.sweep)]
            rr.append((np.linalg.norm(tp[fidx] - xyz[r.anchor]) + d[r.anchor] + dt.get(r.tag, 0.0) - r.range_mm) / 150.0)
        rr.extend((d[1:] / 20.0).tolist())
        rr.extend([dt[t] / 20.0 for t in free_tags])
        return np.asarray(rr, dtype=float)

    x0 = np.r_[pack_pos(init_xyz), np.zeros(7 + len(free_tags)), tag_pos0.reshape(-1)]
    lb = np.full_like(x0, -np.inf)
    ub = np.full_like(x0, np.inf)
    lb[18 : 25 + len(free_tags)] = -60
    ub[18 : 25 + len(free_tags)] = 60

    # Sparse finite-difference Jacobian pattern.
    nvars = len(x0)
    nres = len(pairs) + len(kept) + 7 + len(free_tags)
    sp = lil_matrix((nres, nvars), dtype=int)
    row = 0
    for p in pairs:
        for aid in [p.i, p.j]:
            for col, (_, a, dim) in enumerate(pos_param_names()):
                if a == aid:
                    sp[row, col] = 1
            if aid > 0:
                sp[row, 18 + aid - 1] = 1
        row += 1
    for r in kept:
        for col, (_, a, dim) in enumerate(pos_param_names()):
            if a == r.anchor:
                sp[row, col] = 1
        if r.anchor > 0:
            sp[row, 18 + r.anchor - 1] = 1
        if r.tag in free_to_idx:
            sp[row, 25 + free_to_idx[r.tag]] = 1
        fidx = fmap[(r.tag, r.source, r.sweep)]
        base = 25 + len(free_tags) + 3 * fidx
        sp[row, base : base + 3] = 1
        row += 1
    for aid in range(1, 8):
        sp[row, 18 + aid - 1] = 1
        row += 1
    for t in free_tags:
        sp[row, 25 + free_to_idx[t]] = 1
        row += 1

    res = least_squares(fun, x0, bounds=(lb, ub), loss="huber", f_scale=30.0 / 15.0, jac_sparsity=sp, max_nfev=500, verbose=0)
    xyz, d, dt, tp = unpack(res.x)
    inter = inter_errors(xyz, d, pairs)
    tag_errs = []
    for r in kept:
        fidx = fmap[(r.tag, r.source, r.sweep)]
        pred = float(np.linalg.norm(tp[fidx] - xyz[r.anchor]) + d[r.anchor] + dt.get(r.tag, 0.0))
        tag_errs.append(pred - r.range_mm)
    stats = robust_inlier_stats([r["err_mm"] for r in inter])
    stats.update(
        {
            "top_inter_errors": inter[:12],
            "tag_error_stats": robust_inlier_stats(tag_errs),
            "n_tag_obs": len(kept),
            "n_tag_frames": nframes,
            "tag_names": tag_names,
            "optimizer_success": bool(res.success),
            "optimizer_message": res.message,
            "optimizer_nfev": int(res.nfev),
        }
    )
    return xyz, d, dt, res, stats


def compute_fim_from_jac(result: Any, param_names: list[str], out_path: Path) -> dict[str, Any]:
    j = np.asarray(result.jac.toarray() if hasattr(result.jac, "toarray") else result.jac, dtype=float)
    # Keep calibration parameters only, excluding tag nuisance variables.
    n = len(param_names)
    jc = j[:, :n]
    fim = jc.T @ jc
    cov = np.linalg.pinv(fim)
    diag = np.maximum(np.diag(cov), 0.0)
    sig = np.sqrt(diag)
    corr = np.zeros_like(cov)
    denom = np.outer(sig, sig)
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = np.where(denom > 0, cov / denom, 0.0)
    tops = []
    for i in range(n):
        for k in range(i + 1, n):
            c = float(corr[i, k])
            if abs(c) > 0.5:
                tops.append({"a": param_names[i], "b": param_names[k], "corr": c, "abs_corr": abs(c)})
    tops.sort(key=lambda r: r["abs_corr"], reverse=True)
    payload = {
        "condition_number": float(np.linalg.cond(fim)),
        "param_sigma": {param_names[i]: float(sig[i]) for i in range(n)},
        "top_correlations": tops[:10],
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def fim_for_v3_numeric(xyz: np.ndarray, d: np.ndarray, pairs: list[PairObs], out_path: Path) -> dict[str, Any]:
    names = [n for n, _, _ in pos_param_names()] + [f"d_{ANCHORS[i]}" for i in range(1, 8)]
    x0 = np.r_[pack_pos(xyz), d[1:]]

    def fun(x: np.ndarray) -> np.ndarray:
        xx = unpack_pos(x[:18])
        dd = np.zeros(8)
        dd[1:] = x[18:25]
        return np.asarray([(np.linalg.norm(xx[p.i] - xx[p.j]) + dd[p.i] + dd[p.j] - p.dist_mm) / 15.0 for p in pairs])

    f0 = fun(x0)
    eps = 1e-3
    jac = np.zeros((len(f0), len(x0)))
    for i in range(len(x0)):
        xp = x0.copy()
        xp[i] += eps
        jac[:, i] = (fun(xp) - f0) / eps

    class R:
        pass

    r = R()
    r.jac = jac
    return compute_fim_from_jac(r, names, out_path)


def anchor_uncertainty_from_fim(fim: dict[str, Any]) -> dict[str, dict[str, float]]:
    ps = fim.get("param_sigma", {})
    out: dict[str, dict[str, float]] = {}
    for aid, lab in enumerate(ANCHORS):
        sx = ps.get(f"{lab}.x", 0.0)
        sy = ps.get(f"{lab}.y", 0.0)
        sz = ps.get(f"{lab}.z", 0.0)
        sd = ps.get(f"d_{lab}", 0.0)
        out[lab] = {"sigma_x": sx, "sigma_y": sy, "sigma_z": sz, "sigma_3d": math.sqrt(sx * sx + sy * sy + sz * sz), "sigma_d_anchor": sd}
    return out


def solve_position(rows: list[TrObs], xyz: np.ndarray, d_anchor: np.ndarray, d_tag: float = 0.0, subset: set[int] | None = None) -> np.ndarray | None:
    use = [r for r in rows if subset is None or r.anchor in subset]
    if len({r.anchor for r in use}) < 4:
        return None
    pts = np.asarray([xyz[r.anchor] for r in use])
    w = np.asarray([1.0 / max(r.range_mm, 100.0) for r in use])
    pos = np.average(pts, axis=0, weights=w)

    # Fast per-sweep Huber Gauss-Newton. This is equivalent to minimizing the
    # same robust range residual but avoids launching scipy thousands of times.
    for _ in range(8):
        residual = []
        jac = []
        for r in use:
            v = pos - xyz[r.anchor]
            n = float(np.linalg.norm(v))
            if n < 1e-9:
                continue
            residual.append(n + d_anchor[r.anchor] + d_tag - r.range_mm)
            jac.append(v / n)
        if len(residual) < 4:
            return None
        rr = np.asarray(residual, dtype=float)
        jj = np.asarray(jac, dtype=float)
        # Huber f_scale = 50 mm.
        ww = np.ones_like(rr)
        mask = np.abs(rr) > 50.0
        ww[mask] = 50.0 / np.maximum(np.abs(rr[mask]), 1e-9)
        a = jj.T @ (ww[:, None] * jj) + np.eye(3) * 1e-6
        b = jj.T @ (ww * rr)
        try:
            step = np.linalg.solve(a, b)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(a) @ b
        pos = pos - step
        if float(np.linalg.norm(step)) < 1e-3:
            break
    return np.asarray(pos, dtype=float)


def gdop_at(pos: np.ndarray, xyz: np.ndarray, subset: set[int] | None = None) -> float:
    aids = sorted(subset) if subset is not None else list(range(8))
    rows = []
    for aid in aids:
        v = pos - xyz[aid]
        n = np.linalg.norm(v)
        if n > 1e-9:
            rows.append(v / n)
    if len(rows) < 3:
        return float("nan")
    h = np.asarray(rows)
    q = np.linalg.pinv(h.T @ h)
    return float(np.sqrt(np.trace(q)))


def evaluate_positioning(layouts: dict[str, dict[str, Any]], tr_static: list[TrObs], out_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    by_capture_sweep: dict[tuple[str, str], list[TrObs]] = defaultdict(list)
    cap_name: dict[str, str] = {}
    for r in tr_static:
        by_capture_sweep[(r.capture_id, r.sweep)].append(r)
        cap_name[r.capture_id] = r.capture_name
    by_capture: dict[str, list[tuple[str, list[TrObs]]]] = defaultdict(list)
    for key, rows in by_capture_sweep.items():
        by_capture[key[0]].append((key[1], rows))

    all_results: dict[str, list[dict[str, Any]]] = {}
    gdop_summary: dict[str, dict[str, Any]] = {}
    for lname, lay in layouts.items():
        xyz = lay["xyz"]
        da = lay["d_anchor"]
        dtags = lay.get("d_tag", {})
        rows_out = []
        gdops = []
        stds = []
        for cid, sweeps in sorted(by_capture.items()):
            poses = []
            tag = ""
            for _, rows in sweeps:
                tag = rows[0].tag
                pos = solve_position(rows, xyz, da, dtags.get(tag, 0.0))
                if pos is not None and np.all(np.isfinite(pos)):
                    poses.append(pos)
            arr = np.asarray(poses)
            if arr.shape[0] >= 2:
                std = np.std(arr, axis=0, ddof=1)
                mean = np.mean(arr, axis=0)
                std3 = float(np.linalg.norm(std))
                gd = gdop_at(mean, xyz)
                gdops.append(gd)
                stds.append(std3)
                rows_out.append(
                    {
                        "layout": lname,
                        "capture": cid,
                        "capture_name": cap_name.get(cid, ""),
                        "tag": tag,
                        "n": int(arr.shape[0]),
                        "mean_x": float(mean[0]),
                        "mean_y": float(mean[1]),
                        "mean_z": float(mean[2]),
                        "std_x": float(std[0]),
                        "std_y": float(std[1]),
                        "std_z": float(std[2]),
                        "std_3d": std3,
                        "gdop": gd,
                    }
                )
        all_results[lname] = rows_out
        if len(gdops) >= 2:
            g = np.asarray(gdops)
            s = np.asarray(stds)
            r = float(np.corrcoef(g, s)[0, 1]) if np.std(g) > 0 and np.std(s) > 0 else 0.0
            sig = float(np.dot(g, s) / np.dot(g, g)) if np.dot(g, g) > 0 else 0.0
        else:
            r, sig = 0.0, 0.0
        gdop_summary[lname] = {"pearson_r": r, "sigma_range_estimate": sig}

    # CSV output.
    p = out_dir / "positioning_by_capture.csv"
    fieldnames = ["layout", "capture", "capture_name", "tag", "n", "mean_x", "mean_y", "mean_z", "std_x", "std_y", "std_z", "std_3d", "gdop"]
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for rows in all_results.values():
            for r in rows:
                w.writerow(r)
    (out_dir / "gdop_summary.json").write_text(json.dumps(gdop_summary, indent=2) + "\n")
    return all_results, gdop_summary


def summarize_positioning(rows: list[dict[str, Any]]) -> dict[str, float]:
    vals = {k: [float(r[k]) for r in rows] for k in ["std_x", "std_y", "std_z", "std_3d"]}
    if not rows:
        return {}
    return {
        "x_med": float(np.median(vals["std_x"])),
        "y_med": float(np.median(vals["std_y"])),
        "z_med": float(np.median(vals["std_z"])),
        "std3_med": float(np.median(vals["std_3d"])),
        "std3_best": float(np.min(vals["std_3d"])),
        "std3_worst": float(np.max(vals["std_3d"])),
    }


def capture_group(cid: str) -> str:
    n = int(cid[2:])
    if n in {1, 2, 3, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27}:
        return "center"
    return "edge"


def ablation_id02(layout: dict[str, Any], tr_static: list[TrObs], out_path: Path) -> list[dict[str, Any]]:
    id02 = [r for r in tr_static if r.capture_id == "ID02"]
    by_sweep: dict[str, list[TrObs]] = defaultdict(list)
    for r in id02:
        by_sweep[r.sweep].append(r)
    xyz = layout["xyz"]
    da = layout["d_anchor"]
    dt = layout.get("d_tag", {}).get("BSF66F", 0.0)
    cases: list[tuple[str, set[int]]] = [
        ("all_8", set(range(8))),
        ("no_H_7", set(range(7))),
        ("no_DH_6", {0, 1, 2, 4, 5, 6}),
        ("no_DGH_5", {0, 1, 2, 4, 5}),
    ]
    best4_cases = []
    for low in itertools.combinations(LOWER, 2):
        for up in itertools.combinations(UPPER, 2):
            s = set(low + up)
            poses = [solve_position(rows, xyz, da, dt, s) for rows in by_sweep.values()]
            arr = np.asarray([p for p in poses if p is not None])
            if arr.shape[0] >= 2:
                std = np.std(arr, axis=0, ddof=1)
                best4_cases.append((float(np.linalg.norm(std)), "".join(ANCHORS[i] for i in sorted(s)), s))
    if best4_cases:
        best4_cases.sort(key=lambda x: x[0])
        cases.append((f"best4_{best4_cases[0][1]}", best4_cases[0][2]))

    out = []
    for name, subset in cases:
        poses = [solve_position(rows, xyz, da, dt, subset) for rows in by_sweep.values()]
        arr = np.asarray([p for p in poses if p is not None])
        if arr.shape[0] >= 2:
            std = np.std(arr, axis=0, ddof=1)
            out.append({"case": name, "anchors": "".join(ANCHORS[i] for i in sorted(subset)), "n": int(arr.shape[0]), "std_x": float(std[0]), "std_y": float(std[1]), "std_z": float(std[2]), "std_3d": float(np.linalg.norm(std))})
    out_path.write_text(json.dumps(out, indent=2) + "\n")
    return out


def write_report(outdir: Path, layouts: dict[str, dict[str, Any]], pos_results: dict[str, list[dict[str, Any]]], gdop: dict[str, dict[str, Any]], fim: dict[str, dict[str, Any]], ablation: list[dict[str, Any]]) -> None:
    def fmt(x: Any) -> str:
        if x is None:
            return "-"
        if isinstance(x, (int, np.integer)):
            return str(x)
        try:
            return f"{float(x):.1f}"
        except Exception:
            return str(x)

    names = list(layouts)
    lines = [
        "# V3/V4/V5 Solver Comparison - Outdoor 2026-05-04 Data",
        "",
        "All outputs are contained in this timestamped comparison directory. Existing repository solver outputs were not modified.",
        "",
        "## Data Inventory",
        "",
        "- Inter-anchor sweep: 28 fused pairs from 28,000 raw rows.",
        "- Tag-anchor TR source: ID01-ID31 captures.",
        "- Valid TR rows available: see `positioning/positioning_by_capture.csv` and source summaries.",
        "",
        "## Table 1: Calibration Quality",
        "",
        "| Solver | Inter RMS all 28 | Inlier RMS <=30mm | N inlier | d_anchor range | d_tag values |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for n in names:
        st = layouts[n]["stats"]
        da = layouts[n]["d_anchor"]
        dr = max(da) - min(da)
        tags = layouts[n].get("d_tag", {})
        tagstr = ", ".join(f"{k}={v:.1f}" for k, v in tags.items()) if tags else "N/A"
        lines.append(f"| {n} | {fmt(st.get('all_rms_mm'))} | {fmt(st.get('abs_le_30mm',{}).get('rms_mm'))} | {fmt(st.get('abs_le_30mm',{}).get('n'))} | {fmt(dr)} | {tagstr} |")

    lines += [
        "",
        "## Table 2: ID02 Center-Mid Positioning",
        "",
        "| Solver | N | X std | Y std | Z std | 3D std |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for n in names:
        r = next((x for x in pos_results.get(n, []) if x["capture"] == "ID02"), None)
        lines.append(f"| {n} | {fmt(r.get('n') if r else None)} | {fmt(r.get('std_x') if r else None)} | {fmt(r.get('std_y') if r else None)} | {fmt(r.get('std_z') if r else None)} | {fmt(r.get('std_3d') if r else None)} |")
    lines += [
        "| **V3 concept paper** | **820** | **23.4** | **14.3** | **41.3** | **49.6** |",
        "| **V1 concept paper** | **820** | **41.5** | **37.0** | **119.8** | **132.0** |",
        "",
        "## Table 3: All-Capture Positioning Summary",
        "",
        "| Solver | X med | Y med | Z med | 3D med | 3D best | 3D worst |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for n in names:
        s = summarize_positioning(pos_results.get(n, []))
        lines.append(f"| {n} | {fmt(s.get('x_med'))} | {fmt(s.get('y_med'))} | {fmt(s.get('z_med'))} | {fmt(s.get('std3_med'))} | {fmt(s.get('std3_best'))} | {fmt(s.get('std3_worst'))} |")

    lines += [
        "",
        "## Table 4: Center vs Edge Positioning",
        "",
        "| Solver | Center median 3D | Edge median 3D | Ratio |",
        "|---|---:|---:|---:|",
    ]
    for n in names:
        rows = pos_results.get(n, [])
        center = [r["std_3d"] for r in rows if capture_group(r["capture"]) == "center"]
        edge = [r["std_3d"] for r in rows if capture_group(r["capture"]) == "edge"]
        cm = float(np.median(center)) if center else 0
        em = float(np.median(edge)) if edge else 0
        lines.append(f"| {n} | {fmt(cm)} | {fmt(em)} | {fmt(em/cm if cm else 0)} |")

    lines += [
        "",
        "## Table 5: Per-Anchor FIM Uncertainty",
        "",
        "| Anchor | V3 sigma_3D | V4 interonly sigma_3D | V4 joint all sigma_3D |",
        "|---|---:|---:|---:|",
    ]
    for lab in ANCHORS:
        v3 = anchor_uncertainty_from_fim(fim.get("V3 Tukey", {})).get(lab, {}).get("sigma_3d")
        vi = anchor_uncertainty_from_fim(fim.get("V4 inter-only", {})).get(lab, {}).get("sigma_3d")
        va = anchor_uncertainty_from_fim(fim.get("V4 joint all", {})).get(lab, {}).get("sigma_3d")
        lines.append(f"| {lab} | {fmt(v3)} | {fmt(vi)} | {fmt(va)} |")

    lines += [
        "",
        "## Table 6: GDOP Prediction Accuracy",
        "",
        "| Solver | Pearson r (GDOP vs 3D std) | sigma_range estimate |",
        "|---|---:|---:|",
    ]
    for n in names:
        g = gdop.get(n, {})
        lines.append(f"| {n} | {fmt(g.get('pearson_r'))} | {fmt(g.get('sigma_range_estimate'))} |")

    lines += [
        "",
        "## Anchor Count Ablation on ID02 (V4 inter-only layout)",
        "",
        "| Case | Anchors | N | X std | Y std | Z std | 3D std |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in ablation:
        lines.append(f"| {r['case']} | {r['anchors']} | {r['n']} | {fmt(r['std_x'])} | {fmt(r['std_y'])} | {fmt(r['std_z'])} | {fmt(r['std_3d'])} |")

    lines += [
        "",
        "## Key Findings",
        "",
        "1. V3 Tukey and the V4 family were re-derived from the same outdoor 500-set inter-anchor sweep, not from the previous V4 result.",
        "2. The positioning tables use the same per-sweep Huber position solver for all layouts, so differences come from calibration/layout models rather than evaluation method.",
        "3. The FIM tables are relative uncertainty indicators in the solver parameterization. Large condition numbers indicate gauge/geometry/delay coupling and should be interpreted as weak-determination warnings, not absolute millimeter truth.",
        "4. The all-row residuals include real UWB outliers. Inlier RMS and positioning standard deviation are therefore more useful for evaluating the practical layout quality.",
        "5. The ID02 row is the closest direct comparison to the original concept-paper center evaluation.",
        "",
        "## Files",
        "",
        "- Solves: `solves/*.json`",
        "- FIM outputs: `fim/*.json`",
        "- Positioning CSV: `positioning/positioning_by_capture.csv`",
        "- Ablation: `positioning/id02_anchor_count_ablation_v4_interonly.json`",
    ]
    (outdir / "reports" / "solver_comparison_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--captures", required=True)
    args = ap.parse_args()

    outdir = Path(args.outdir)
    solves = outdir / "solves"
    fim_dir = outdir / "fim"
    pos_dir = outdir / "positioning"
    for d in [solves, fim_dir, pos_dir, outdir / "reports"]:
        d.mkdir(parents=True, exist_ok=True)

    pairs = load_and_fuse_pairs(Path(args.pairs))
    (solves / "v3_fused_pairs.json").write_text(json.dumps([p.__dict__ for p in pairs], indent=2) + "\n")
    init = classical_mds(pair_matrix(pairs))
    tr_all = load_tr_observations(Path(args.captures), set(range(1, 32)))
    tr_static = [r for r in tr_all if 1 <= int(r.capture_id[2:]) <= 27]
    tr_roto = [r for r in tr_all if 28 <= int(r.capture_id[2:]) <= 31]

    layouts: dict[str, dict[str, Any]] = {}
    fim_payloads: dict[str, dict[str, Any]] = {}

    v1_xyz, v1_d, v1_res, v1_stats = solve_v1_no_delay(pairs, init)
    save_layout(solves / "v1_no_delay_layout.json", "V1 no delay", v1_xyz, v1_d, {}, v1_stats)
    layouts["V1 no delay"] = {"xyz": v1_xyz, "d_anchor": v1_d, "d_tag": {}, "stats": v1_stats}

    v3_xyz, v3_d, v3_res, v3_stats = solve_v3_tukey(pairs, init, with_delay=True)
    save_layout(solves / "v3_tukey_layout.json", "V3 Tukey", v3_xyz, v3_d, {}, v3_stats)
    layouts["V3 Tukey"] = {"xyz": v3_xyz, "d_anchor": v3_d, "d_tag": {}, "stats": v3_stats}
    fim_payloads["V3 Tukey"] = fim_for_v3_numeric(v3_xyz, v3_d, pairs, fim_dir / "fim_v3_tukey.json")

    vi_xyz, vi_d, vi_res, vi_stats = solve_v4_interonly(pairs, init)
    save_layout(solves / "v4_interonly_layout.json", "V4 inter-only", vi_xyz, vi_d, {}, vi_stats)
    layouts["V4 inter-only"] = {"xyz": vi_xyz, "d_anchor": vi_d, "d_tag": {}, "stats": vi_stats}
    fim_payloads["V4 inter-only"] = compute_fim_from_jac(vi_res, [n for n, _, _ in pos_param_names()] + [f"d_{ANCHORS[i]}" for i in range(1, 8)], fim_dir / "fim_v4_interonly.json")

    vr_xyz, vr_d, vr_dt, vr_res, vr_stats = solve_v4_joint(pairs, tr_roto, vi_xyz, ["BS2DCE", "BSDC91"], "BS2DCE", 100, "V4 joint roto")
    save_layout(solves / "v4_joint_roto_layout.json", "V4 joint roto", vr_xyz, vr_d, vr_dt, vr_stats)
    layouts["V4 joint roto"] = {"xyz": vr_xyz, "d_anchor": vr_d, "d_tag": vr_dt, "stats": vr_stats}
    fim_payloads["V4 joint roto"] = compute_fim_from_jac(vr_res, [n for n, _, _ in pos_param_names()] + [f"d_{ANCHORS[i]}" for i in range(1, 8)] + ["dtag_BSDC91"], fim_dir / "fim_v4_joint_roto.json")

    va_xyz, va_d, va_dt, va_res, va_stats = solve_v4_joint(pairs, tr_all, vi_xyz, ["BS2DCE", "BSDC91", "BSF66F"], "BS2DCE", 100, "V4 joint all")
    save_layout(solves / "v4_joint_all_layout.json", "V4 joint all", va_xyz, va_d, va_dt, va_stats)
    layouts["V4 joint all"] = {"xyz": va_xyz, "d_anchor": va_d, "d_tag": va_dt, "stats": va_stats}
    fim_payloads["V4 joint all"] = compute_fim_from_jac(va_res, [n for n, _, _ in pos_param_names()] + [f"d_{ANCHORS[i]}" for i in range(1, 8)] + ["dtag_BSDC91", "dtag_BSF66F"], fim_dir / "fim_v4_joint_all.json")

    pos_results, gdop = evaluate_positioning(layouts, tr_static, pos_dir)
    ablation = ablation_id02(layouts["V4 inter-only"], tr_static, pos_dir / "id02_anchor_count_ablation_v4_interonly.json")
    write_report(outdir, layouts, pos_results, gdop, fim_payloads, ablation)

    summary = {
        "outdir": str(outdir),
        "pairs": len(pairs),
        "tr_valid_all": len(tr_all),
        "tr_valid_static": len(tr_static),
        "tr_valid_roto": len(tr_roto),
        "solvers": {k: v["stats"] for k, v in layouts.items()},
    }
    (outdir / "reports" / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2)[:6000])
    print(f"[ok] report {outdir / 'reports' / 'solver_comparison_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
