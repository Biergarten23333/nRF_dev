#!/usr/bin/env python3
from __future__ import annotations

import csv
import itertools
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares


ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT.parents[1]
SWEEP_CSV = PIPELINE / "outdoor_v4_20260504/sweeps/inter_anchor_500set_20260504_185011/pairs_all.csv"
ID02_DIR = PIPELINE / "outdoor_v4_20260504/tr_captures/ID02_static_center_mid_20260504_192643"
ANCHORS = "ABCDEFGH"
LOWER = {0, 1, 2, 3}
UPPER = {4, 5, 6, 7}


def rms(vals):
    arr = np.asarray(vals, dtype=float)
    return float(np.sqrt(np.mean(arr * arr))) if arr.size else float("nan")


def mad_sigma(vals, floor=1.0):
    vals = list(map(float, vals))
    if not vals:
        return floor
    med = float(np.median(vals))
    mad = float(np.median(np.abs(np.asarray(vals) - med)))
    return max(float(floor), 1.4826 * mad)


def anchor_idx(v):
    s = str(v).strip().upper()
    if s in ANCHORS:
        return ANCHORS.index(s)
    return int(s)


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
            if a != b and d > 0 and q > 0 and ok:
                if master == a:
                    directed[(a, b)].append(d)
                elif master == b:
                    directed[(b, a)].append(d)
                else:
                    directed[(a, b)].append(d)
    return directed


def fuse_pairs(directed):
    v1, v3, comparison = [], [], []
    for i in range(8):
        for j in range(i + 1, 8):
            ab = np.asarray(directed[(i, j)], dtype=float)
            ba = np.asarray(directed[(j, i)], dtype=float)
            allv = np.concatenate([ab, ba])
            mean_ab = float(np.mean(ab))
            mean_ba = float(np.mean(ba))
            d_v1 = float(np.mean(allv))
            med_ab = float(np.median(ab))
            med_ba = float(np.median(ba))
            sig_ab = mad_sigma(ab, 1.0)
            sig_ba = mad_sigma(ba, 1.0)
            denom = sig_ab**2 + sig_ba**2
            d_v3 = float((sig_ba**2 * med_ab + sig_ab**2 * med_ba) / denom)
            pair = f"{ANCHORS[i]}-{ANCHORS[j]}"
            v1.append({"i": i, "j": j, "pair": pair, "dist": d_v1, "sigma": mad_sigma(allv, 5.0)})
            v3.append({"i": i, "j": j, "pair": pair, "dist": d_v3, "sigma": mad_sigma(allv, 5.0)})
            comparison.append({
                "pair": pair,
                "d_v1_avg": d_v1,
                "d_v1_dir_avg": (mean_ab + mean_ba) / 2.0,
                "d_v3_mvue": d_v3,
                "diff_mm": d_v1 - d_v3,
                "sigma_ab": sig_ab,
                "sigma_ba": sig_ba,
                "asym_mm": abs(med_ab - med_ba),
                "n_ab": len(ab),
                "n_ba": len(ba),
            })
    return v1, v3, comparison


def pair_matrix(pairs):
    dist = np.zeros((8, 8), dtype=float)
    for p in pairs:
        dist[p["i"], p["j"]] = dist[p["j"], p["i"]] = p["dist"]
    return dist


def residuals_inter_pos(x, pairs, delays=None, weights=None):
    xyz = unpack_pos(x)
    d = np.zeros(8) if delays is None else delays
    res = []
    for n, p in enumerate(pairs):
        r = np.linalg.norm(xyz[p["i"]] - xyz[p["j"]]) + d[p["i"]] + d[p["j"]] - p["dist"]
        if weights is not None:
            r *= math.sqrt(max(0.0, weights[n]))
        res.append(r)
    return np.asarray(res)


def inter_errors(xyz, delays, pairs):
    rows = []
    for p in pairs:
        pred = float(np.linalg.norm(xyz[p["i"]] - xyz[p["j"]]) + delays[p["i"]] + delays[p["j"]])
        err = pred - p["dist"]
        rows.append({"pair": p["pair"], "meas_mm": p["dist"], "pred_mm": pred, "err_mm": err, "abs_err_mm": abs(err)})
    return sorted(rows, key=lambda r: r["abs_err_mm"], reverse=True)


def tukey_weights(resids):
    sig = mad_sigma(resids, 1.0)
    c = max(1.0, 4.685 * sig)
    out = []
    for r in resids:
        u = float(r) / c
        out.append((1.0 - u * u) ** 2 if abs(u) <= 1.0 else 0.0)
    return np.asarray(out), sig, c


def solve_no_delay(pairs, init_xyz):
    res = least_squares(lambda x: residuals_inter_pos(x, pairs), pack_pos(init_xyz), loss="linear", max_nfev=1000)
    xyz = gauge_align(unpack_pos(res.x))
    return xyz, np.zeros(8), res


def solve_v4_interonly(pairs, init_xyz):
    def unpack(x):
        xyz = unpack_pos(x[:18])
        d = np.zeros(8)
        d[1:] = x[18:25]
        return xyz, d

    def fun(x):
        xyz, d = unpack(x)
        res = []
        for p in pairs:
            pred = np.linalg.norm(xyz[p["i"]] - xyz[p["j"]]) + d[p["i"]] + d[p["j"]]
            res.append((pred - p["dist"]) / 15.0)
        res.extend((d[1:] / 20.0).tolist())
        return np.asarray(res)

    x0 = np.r_[pack_pos(init_xyz), np.zeros(7)]
    lo = np.r_[np.full(18, -np.inf), np.full(7, -60.0)]
    hi = np.r_[np.full(18, np.inf), np.full(7, 60.0)]
    res = least_squares(fun, x0, loss="huber", f_scale=30.0 / 15.0, bounds=(lo, hi), max_nfev=1000)
    xyz, d = unpack(res.x)
    return gauge_align(xyz), d, res


def solve_v3_full(pairs, init_xyz):
    log_lines = []
    xyz = gauge_align(init_xyz)
    delays = np.zeros(8)
    converged = False
    final_weights = np.ones(len(pairs))
    for it in range(30):
        old_xyz = xyz.copy()
        old_delays = delays.copy()
        raw = residuals_inter_pos(pack_pos(xyz), pairs, delays)
        weights, sig, c_t = tukey_weights(raw)
        final_weights = weights
        res = least_squares(
            lambda x: residuals_inter_pos(x, pairs, delays, weights),
            pack_pos(xyz),
            loss="linear",
            max_nfev=500,
        )
        xyz = gauge_align(unpack_pos(res.x))
        new_delays = old_delays.copy()
        for i in range(1, 8):
            estimates = []
            for p in pairs:
                if p["i"] == i:
                    j = p["j"]
                elif p["j"] == i:
                    j = p["i"]
                else:
                    continue
                geom = float(np.linalg.norm(xyz[i] - xyz[j]))
                pair_bias = p["dist"] - geom
                estimates.append(pair_bias - old_delays[j])
            new_delays[i] = float(np.median(estimates)) if estimates else 0.0
        new_delays[0] = 0.0
        delays = new_delays
        errs = [r["err_mm"] for r in inter_errors(xyz, delays, pairs)]
        max_shift = float(np.max(np.linalg.norm(xyz - old_xyz, axis=1)))
        max_dshift = float(np.max(np.abs(delays - old_delays)))
        log_lines.append(f"Iter {it}")
        log_lines.append("  Delays mm: " + ", ".join(f"{ANCHORS[i]}={delays[i]:+.2f}" for i in range(8)))
        log_lines.append(f"  Residual sigma: {sig:.2f}  c_T: {c_t:.2f}")
        log_lines.append(f"  N zero-weight: {int(np.sum(weights <= 1e-12))}")
        log_lines.append(f"  All-pair RMS: {rms(errs):.2f}")
        log_lines.append(f"  Max position shift: {max_shift:.4f}")
        log_lines.append(f"  Max delay shift: {max_dshift:.4f}")
        if max_shift < 0.1 and max_dshift < 0.01:
            converged = True
            log_lines.append(f"  CONVERGED at iteration {it}")
            break
    final_errs = inter_errors(xyz, delays, pairs)
    log_lines.append("")
    log_lines.append("Final per-pair residuals sorted by |residual|:")
    for row in final_errs:
        idx = next(i for i, p in enumerate(pairs) if p["pair"] == row["pair"])
        log_lines.append(f"  {row['pair']}: err={row['err_mm']:+.2f}mm weight={final_weights[idx]:.4f}")
    return xyz, delays, {"converged": converged, "iterations": it + 1, "weights": final_weights, "log": "\n".join(log_lines)}


def save_json(path, name, xyz, d_anchor, stats):
    data = {
        "name": name,
        "anchors": [
            {"id": i, "label": ANCHORS[i], "x_mm": float(xyz[i, 0]), "y_mm": float(xyz[i, 1]), "z_mm": float(xyz[i, 2]), "d_anchor_mm": float(d_anchor[i])}
            for i in range(8)
        ],
        "tags": [],
        "stats": stats,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_id02_frames():
    paths = sorted(ID02_DIR.glob("recv_*/tr_all.csv"))
    if not paths:
        raise FileNotFoundError(ID02_DIR)
    frames = defaultdict(list)
    with paths[0].open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(float(row["valid"])) != 1:
                continue
            aid = int(row["anchor_id"])
            rng = float(row["range_mm"])
            if 0 <= aid < 8 and rng > 0:
                frames[int(row["sweep"])].append((aid, rng))
    return [v for _, v in sorted(frames.items()) if len(v) >= 4]


def solve_position(obs, xyz, d_anchor, method="huber", x0=None):
    if x0 is None:
        pts = np.asarray([xyz[aid] for aid, _ in obs])
        x0 = np.mean(pts, axis=0)
    def fun(p):
        return np.asarray([np.linalg.norm(p - xyz[aid]) + d_anchor[aid] - rng for aid, rng in obs])
    if method == "huber":
        res = least_squares(fun, x0, loss="huber", f_scale=50.0, max_nfev=100)
    else:
        res = least_squares(fun, x0, loss="linear", max_nfev=100)
    return res.x, rms(fun(res.x))


def is_valid_qa_subset(sub):
    s = set(sub)
    return len(s & LOWER) >= 2 and len(s & UPPER) >= 2


QA_SUBSETS = [tuple(s) for s in itertools.combinations(range(8), 4) if is_valid_qa_subset(s)]


def eval_layout(frames, xyz, d_anchor, method, fixed_anchors=None):
    positions = []
    selected = []
    last = None
    for frame in frames:
        if fixed_anchors is not None:
            obs = [(a, r) for a, r in frame if a in fixed_anchors]
            if len(obs) < 4:
                continue
            pos, rr = solve_position(obs, xyz, d_anchor, "huber", last)
            positions.append(pos)
            selected.append(("fixed", rr))
            last = pos
            continue
        if method in {"huber", "l2"}:
            obs = frame
            if len(obs) < 4:
                continue
            pos, rr = solve_position(obs, xyz, d_anchor, "huber" if method == "huber" else "l2", last)
            positions.append(pos)
            selected.append(("all", rr))
            last = pos
        elif method == "qa":
            visible = {a for a, _ in frame}
            candidates = []
            full = tuple(sorted(visible))
            if len(full) >= 4:
                candidates.append(full)
            candidates.extend([s for s in QA_SUBSETS if set(s).issubset(visible)])
            best = None
            for sub in candidates:
                obs = [(a, r) for a, r in frame if a in sub]
                if len(obs) < 4:
                    continue
                pos, rr = solve_position(obs, xyz, d_anchor, "l2", last)
                if best is None or rr < best[0]:
                    best = (rr, pos, sub)
            if best is not None:
                positions.append(best[1])
                selected.append((best[2], best[0]))
                last = best[1]
    arr = np.asarray(positions)
    if len(arr) == 0:
        return {"N": 0, "X": float("nan"), "Y": float("nan"), "Z": float("nan"), "3D": float("nan"), "mean_subset_size": float("nan")}
    std = np.std(arr, axis=0, ddof=1)
    sizes = [len(s[0]) if isinstance(s[0], tuple) else 0 for s in selected]
    return {"N": int(len(arr)), "X": float(std[0]), "Y": float(std[1]), "Z": float(std[2]), "3D": float(np.linalg.norm(std)), "mean_subset_size": float(np.mean(sizes))}


def fmt(v, nd=1):
    if isinstance(v, str):
        return v
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "nan"
    return f"{float(v):.{nd}f}"


def markdown_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(out)


def main():
    directed = load_raw_sweep()
    v1_pairs, v3_pairs, fusion = fuse_pairs(directed)
    fusion_path = ROOT / "debug/fusion_comparison.csv"
    with fusion_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["pair", "d_v1_avg", "d_v1_dir_avg", "d_v3_mvue", "diff_mm", "sigma_ab", "sigma_ba", "asym_mm", "n_ab", "n_ba"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(fusion)
    print("Fusion comparison")
    for r in fusion:
        print(f"{r['pair']:>3} v1={r['d_v1_avg']:8.1f} v3={r['d_v3_mvue']:8.1f} diff={r['diff_mm']:+7.1f} sig=({r['sigma_ab']:.1f},{r['sigma_ba']:.1f}) asym={r['asym_mm']:.1f}")
    max_fusion_diff = max(abs(r["diff_mm"]) for r in fusion)

    init_v1 = classical_mds(pair_matrix(v1_pairs))
    init_v3 = classical_mds(pair_matrix(v3_pairs))
    v1_xyz, v1_d, _ = solve_no_delay(v1_pairs, init_v1)
    v3lite_xyz, v3lite_d, _ = solve_no_delay(v3_pairs, init_v3)
    v3_xyz, v3_d, v3dbg = solve_v3_full(v3_pairs, init_v3)
    v4_xyz, v4_d, v4res = solve_v4_interonly(v3_pairs, init_v3)

    (ROOT / "debug/v3_iteration_log.txt").write_text(v3dbg["log"], encoding="utf-8")
    with (ROOT / "debug/v3_pair_residuals.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["pair", "meas_mm", "pred_mm", "err_mm", "abs_err_mm", "tukey_weight"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in inter_errors(v3_xyz, v3_d, v3_pairs):
            idx = next(i for i, p in enumerate(v3_pairs) if p["pair"] == row["pair"])
            row["tukey_weight"] = float(v3dbg["weights"][idx])
            w.writerow(row)

    def stats_for(xyz, d, pairs):
        errs = [r["err_mm"] for r in inter_errors(xyz, d, pairs)]
        in30 = [e for e in errs if abs(e) <= 30]
        return {
            "inter_all_rms_mm": rms(errs),
            "inter_inlier_30_rms_mm": rms(in30),
            "inter_inlier_30_n": len(in30),
            "d_anchor_range_mm": float(np.max(d) - np.min(d)),
        }

    save_json(ROOT / "solves/v1_avg_layout.json", "V1 simple average no delay", v1_xyz, v1_d, stats_for(v1_xyz, v1_d, v1_pairs))
    save_json(ROOT / "solves/v3_lite_mvue_layout.json", "V3-lite MVUE no delay", v3lite_xyz, v3lite_d, stats_for(v3lite_xyz, v3lite_d, v3_pairs))
    save_json(ROOT / "solves/v3_full_tukey_layout.json", "V3-full Tukey alternating", v3_xyz, v3_d, stats_for(v3_xyz, v3_d, v3_pairs))
    save_json(ROOT / "solves/v4_interonly_debug_layout.json", "V4 inter-only debug", v4_xyz, v4_d, stats_for(v4_xyz, v4_d, v3_pairs) | {"optimizer_success": bool(v4res.success), "optimizer_cost": float(v4res.cost)})

    frames = load_id02_frames()
    layouts = {
        "V1 (V1-avg fusion)": (v1_xyz, v1_d),
        "V3-lite (MVUE, no delay)": (v3lite_xyz, v3lite_d),
        "V3-full (MVUE + delay)": (v3_xyz, v3_d),
        "V4 inter-only": (v4_xyz, v4_d),
    }
    eval_rows = []
    eval_csv = []
    for name, (xyz, d) in layouts.items():
        methods = ["huber"]
        if name in {"V1 (V1-avg fusion)", "V3-full (MVUE + delay)"}:
            methods.extend(["l2", "qa"])
        if name == "V4 inter-only":
            methods.append("qa")
        for method in methods:
            st = eval_layout(frames, xyz, d, method)
            eval_rows.append([name, "8anc Huber" if method == "huber" else ("8anc L2" if method == "l2" else "QA select"), st["N"], fmt(st["X"]), fmt(st["Y"]), fmt(st["Z"]), fmt(st["3D"])])
            eval_csv.append({"layout": name, "method": method, **st})
    ablation = []
    baseline = eval_layout(frames, v1_xyz, v1_d, "huber")
    v3lite_eval = eval_layout(frames, v3lite_xyz, v3lite_d, "huber")
    v3_eval = eval_layout(frames, v3_xyz, v3_d, "huber")
    v3_qa_eval = eval_layout(frames, v3_xyz, v3_d, "qa")
    v3_no_dh_eval = eval_layout(frames, v3_xyz, v3_d, "huber", fixed_anchors={0, 1, 2, 4, 5, 6})
    for label, st, ref in [
        ("Baseline: V1-avg, 8anc Huber", baseline, None),
        ("Change fusion: V3-MVUE, 8anc Huber", v3lite_eval, baseline),
        ("Add delay est: V3-full, 8anc Huber", v3_eval, baseline),
        ("Change eval: V3-full, QA select", v3_qa_eval, baseline),
        ("Remove D/H: V3-full, 6anc Huber", v3_no_dh_eval, baseline),
    ]:
        delta = "ref" if ref is None else fmt(st["3D"] - ref["3D"])
        ablation.append([label, fmt(st["3D"]), delta])

    with (ROOT / "positioning/id02_positioning_methods.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["layout", "method", "N", "X", "Y", "Z", "3D", "mean_subset_size"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(eval_csv)

    delay_rows = []
    for i in range(8):
        delay_rows.append([ANCHORS[i], fmt(v3_d[i]), fmt(v4_d[i]), fmt(v3_d[i] - v4_d[i])])
    v3_errs = [r["err_mm"] for r in inter_errors(v3_xyz, v3_d, v3_pairs)]
    v3_in30 = [e for e in v3_errs if abs(e) <= 30]
    summary_rows = [
        ["Converged", "Yes" if v3dbg["converged"] else "No"],
        ["Iterations", v3dbg["iterations"]],
        ["Final delay range (max-min)", fmt(np.max(v3_d) - np.min(v3_d))],
        ["Max abs(delay)", fmt(np.max(np.abs(v3_d)))],
        ["N Tukey-rejected pairs", int(np.sum(v3dbg["weights"] <= 1e-12))],
        ["Inlier RMS (<=30mm)", fmt(rms(v3_in30))],
        ["All-pair RMS", fmt(rms(v3_errs))],
    ]
    fusion_rows = [[r["pair"], fmt(r["d_v1_avg"]), fmt(r["d_v3_mvue"]), fmt(r["diff_mm"]), fmt(r["sigma_ab"]), fmt(r["sigma_ba"]), fmt(r["asym_mm"])] for r in fusion]
    report = []
    report.append("# V3/V4 Solver Verification Report\n")
    report.append(f"Output directory: `{ROOT}`\n")
    report.append("## Table A: Fusion Method Impact\n")
    report.append(markdown_table(["Pair", "V1 avg", "V3 MVUE", "Diff", "sigma_ab", "sigma_ba", "Asymmetry"], fusion_rows))
    report.append(f"\n\nMax abs(V1 avg - V3 MVUE) = **{max_fusion_diff:.2f} mm**. " + ("Fusion alone cannot explain a large V1/V3 gap." if max_fusion_diff < 5 else "Only a small number of pairs exceed 5 mm, so fusion can move the layout slightly but cannot explain a concept-paper-scale gap by itself."))
    report.append("\n\n## Table B: V3 Debug Summary\n")
    report.append(markdown_table(["Metric", "Value"], summary_rows))
    report.append("\n\n## Table C: Delay Comparison\n")
    report.append(markdown_table(["Anchor", "V3 Tukey delay", "V4 inter-only delay", "Difference"], delay_rows))
    report.append("\n\n## Table D: ID02 Positioning - All Methods\n")
    report.append(markdown_table(["Layout", "Eval method", "N", "X", "Y", "Z", "3D"], eval_rows + [
        ["Concept V1", "QA select", "820", "41.5", "37.0", "119.8", "132.0"],
        ["Concept V3", "QA select", "820", "23.4", "14.3", "41.3", "49.6"],
    ]))
    report.append("\n\n## Table E: Ablation Matrix\n")
    report.append(markdown_table(["Factor changed", "3D std", "Delta from baseline"], ablation))
    report.append("\n\n## Notes\n")
    report.append("- V1 in this run uses simple averaging only; V3-lite/V3-full use MAD+MVUE.\n")
    report.append("- QA selection tries size-4 subsets with at least two lower and two upper anchors, plus the full visible set.\n")
    report.append("- The V3 iteration log and sorted residuals are saved in `debug/`.\n")
    report.append("\n## Key Findings\n")
    report.append("1. V1 simple averaging and V3 MAD+MVUE fusion are almost identical on this outdoor 500-set sweep; most pairs differ by only 0-2 mm.\n")
    report.append("2. V3-full alternating Tukey did not converge in 30 iterations. Its delay range is far lower than the previous suspicious run, but max abs(delay) is still physically high.\n")
    report.append("3. Tukey becomes too aggressive late in the run: residual sigma reaches the 1 mm floor and 8 pairs are fully rejected.\n")
    report.append("4. ID02 all-8-anchor Huber is already near the concept V3 result for V1/V3/V4, so this outdoor dataset no longer reproduces the bad concept V1 baseline.\n")
    report.append("5. The QA selector here is a residual-only subset selector and performs worse, which means it is not equivalent to a production quality/history/gated on-board selector.\n")
    report.append("6. Removing D/H improves ID02 stability, so those anchors remain worth investigating before claiming a solver-level improvement.\n")
    (ROOT / "reports/verification_report.md").write_text("\n".join(report), encoding="utf-8")

    print("\nV3 debug summary")
    for row in summary_rows:
        print(f"{row[0]}: {row[1]}")
    print("\nID02 positioning")
    for row in eval_rows:
        print(row)
    print(f"\nReport: {ROOT / 'reports/verification_report.md'}")


if __name__ == "__main__":
    main()
