#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import least_squares


ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "autopos_pipeline" / "outdoor_20260513"
OLD_ROOT = ROOT / "autopos_pipeline" / "outdoor_v4_20260504"
OUT = Path(__file__).resolve().parent

ANCHORS = list("ABCDEFGH")
ANCHOR_I = {a: i for i, a in enumerate(ANCHORS)}

WAND_DISTANCES = {
    ("BSCCF4", "BS9336"): 670.0,
    ("BSCCF4", "BS955A"): 659.7,
    ("BS9336", "BS955A"): 708.7,
}

ROTO_R_INNER = 440.0
ROTO_R_OUTER = 560.0
ROTO_DELTA_R = 120.0

STATIC_META = {
    "ID01": ("edge_low", "low", "ABEF"),
    "ID02": ("edge_mid", "mid", "ABEF"),
    "ID03": ("edge_high", "high", "ABEF"),
    "ID04": ("edge_low", "low", "BCGF"),
    "ID05": ("edge_mid", "mid", "BCGF"),
    "ID06": ("edge_high", "high", "BCGF"),
    "ID07": ("edge_low", "low", "CDHG"),
    "ID08": ("edge_mid", "mid", "CDHG"),
    "ID09": ("edge_high", "high", "CDHG"),
    "ID10": ("edge_low", "low", "ADHE"),
    "ID11": ("edge_mid", "mid", "ADHE"),
    "ID12": ("edge_high", "high", "ADHE"),
    "ID13": ("center_mid", "mid", "ABEF"),
    "ID14": ("center_mid", "mid", "BCGF"),
    "ID15": ("center_mid", "mid", "CDHG"),
    "ID16": ("center_mid", "mid", "ADHE"),
    "ID17": ("center_low", "low", "ABEF"),
    "ID18": ("center_low", "low", "BCGF"),
    "ID19": ("center_low", "low", "CDHG"),
    "ID20": ("center_low", "low", "ADHE"),
    "ID21": ("center_high", "high", "ABEF"),
    "ID22": ("center_high", "high", "BCGF"),
    "ID23": ("center_high", "high", "CDHG"),
    "ID24": ("center_high", "high", "ADHE"),
}

ROTO_META = {
    "ID25": ("planar", "none"),
    "ID26": ("small", "ABEF"),
    "ID27": ("small", "BCGF"),
    "ID28": ("small", "CDHG"),
    "ID29": ("small", "ADHE"),
    "ID30": ("mid", "ABEF"),
    "ID31": ("mid", "BCGF"),
    "ID32": ("mid", "CDHG"),
    "ID33": ("mid", "ADHE"),
    "ID34": ("high", "ABEF"),
    "ID35": ("high", "BCGF"),
    "ID36": ("high", "CDHG"),
    "ID37": ("high", "ADHE"),
    "ID38": ("vertical", "ABEF"),
    "ID39": ("vertical", "BCGF"),
    "ID40": ("vertical", "CDHG"),
    "ID41": ("vertical", "ADHE"),
    "ID42": ("extra", "extra"),
}

WAND_META = {
    "W01": "C points ABEF",
    "W02": "C points BCGF",
    "W03": "C points CDHG",
    "W04": "C points ADHE",
    "W05": "free move; analyzed but excluded from strong same-frame rigid constraints",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def ensure_dirs() -> None:
    for sub in ["solves", "positioning", "wand", "roto", "figures", "reports", "tables"]:
        (OUT / sub).mkdir(parents=True, exist_ok=True)


def read_csv_dict(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        keys = []
        for row in rows:
            for k in row:
                if k not in keys:
                    keys.append(k)
        fields = keys
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def mad(x) -> float:
    arr = np.asarray(x, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    med = np.median(arr)
    return float(1.4826 * np.median(np.abs(arr - med)))


def rmse(x) -> float:
    arr = np.asarray(x, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean(arr * arr)))


def latest_id_dirs(base: Path, prefix: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not base.exists():
        return out
    pat = re.compile(rf"^({prefix}\d+)(?:_|$)")
    for d in sorted([p for p in base.iterdir() if p.is_dir()]):
        m = pat.match(d.name)
        if not m:
            continue
        key = m.group(1)
        if (d / "tr_all.csv").exists():
            out[key] = d
    return out


def latest_w_dirs(base: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not base.exists():
        return out
    pat = re.compile(r"^(W\d+)(?:_|$)")
    for d in sorted([p for p in base.iterdir() if p.is_dir()]):
        m = pat.match(d.name)
        if not m:
            continue
        key = m.group(1)
        if (d / "tr_all.csv").exists():
            out[key] = d
    return out


def load_pairs(path: Path):
    rows = read_csv_dict(path)
    data = defaultdict(list)
    for r in rows:
        try:
            a, b = r["a"], r["b"]
            master = r.get("master", "")
            d = float(r.get("dist_mm") or r.get("range_mm") or r.get("raw_mm"))
            q = float(r.get("quality_percent", 100) or 100)
            ok = int(float(r.get("ok", "1") or 1))
        except Exception:
            continue
        if a in ANCHOR_I and b in ANCHOR_I and ok and d > 0:
            data[(a, b, master)].append((d, q))
    return data


def fuse_pairs(path: Path):
    data = load_pairs(path)
    rows = []
    fused_v1 = {}
    fused_v3 = {}
    pair_mads = defaultdict(list)
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1:]:
            ab = [x[0] for x in data.get((a, b, a), [])]
            ba = [x[0] for x in data.get((a, b, b), [])]
            allv = ab + ba
            if not allv:
                # tolerate stored reverse ordering
                ab = [x[0] for x in data.get((b, a, a), [])]
                ba = [x[0] for x in data.get((b, a, b), [])]
                allv = ab + ba
            if not allv:
                continue
            med_ab = float(np.median(ab)) if ab else float("nan")
            med_ba = float(np.median(ba)) if ba else float("nan")
            mad_ab = mad(ab)
            mad_ba = mad(ba)
            v1 = float(np.mean(allv))
            parts = []
            for med, sig, n in [(med_ab, mad_ab, len(ab)), (med_ba, mad_ba, len(ba))]:
                if np.isfinite(med):
                    sig = sig if np.isfinite(sig) and sig > 1e-6 else max(1.0, mad(allv))
                    parts.append((med, sig, max(n, 1)))
            if len(parts) == 1:
                v3 = parts[0][0]
            else:
                weights = np.array([n / (s * s) for _, s, n in parts], dtype=float)
                vals = np.array([m for m, _, _ in parts], dtype=float)
                v3 = float(np.sum(weights * vals) / np.sum(weights))
            key = (a, b)
            fused_v1[key] = v1
            fused_v3[key] = v3
            for sig in [mad_ab, mad_ba]:
                if np.isfinite(sig):
                    pair_mads[a].append(sig)
                    pair_mads[b].append(sig)
            rows.append({
                "pair": a + b,
                "a": a,
                "b": b,
                "n_ab": len(ab),
                "n_ba": len(ba),
                "med_ab": med_ab,
                "med_ba": med_ba,
                "mad_ab": mad_ab,
                "mad_ba": mad_ba,
                "asymmetry": abs(med_ab - med_ba) if np.isfinite(med_ab) and np.isfinite(med_ba) else float("nan"),
                "v1_fused": v1,
                "v3_fused": v3,
                "v1_v3_diff": v1 - v3,
            })
    anchor_sigma = {}
    for a in ANCHORS:
        s = np.median(pair_mads[a]) if pair_mads[a] else 50.0
        anchor_sigma[a] = float(max(5.0, s))
    return rows, fused_v1, fused_v3, anchor_sigma


def pack_layout(x: np.ndarray) -> dict[str, np.ndarray]:
    layout = {
        "A": np.array([0.0, 0.0, 0.0]),
        "B": np.array([x[0], 0.0, 0.0]),
        "C": np.array([x[1], x[2], 0.0]),
    }
    k = 3
    for a in ANCHORS[3:]:
        layout[a] = np.array(x[k:k + 3], dtype=float)
        k += 3
    return layout


def layout_to_json(layout: dict[str, np.ndarray], delays=None):
    return {
        "anchors": {a: [float(v) for v in layout[a]] for a in ANCHORS},
        "delays_mm": {a: float(delays.get(a, 0.0)) for a in ANCHORS} if delays else {a: 0.0 for a in ANCHORS},
        "units": "mm",
        "gauge": "A=(0,0,0), B=(Bx,0,0), C=(Cx,Cy,0)",
    }


def initial_layout_from_dist(fused: dict[tuple[str, str], float]) -> np.ndarray:
    d = lambda a, b: fused.get(tuple(sorted((a, b))), 3000.0)
    ab = d("A", "B")
    ac = d("A", "C")
    bc = d("B", "C")
    bx = ab
    cx = (ac * ac + ab * ab - bc * bc) / (2 * ab) if ab > 1 else 0.0
    cy = math.sqrt(max(1.0, ac * ac - cx * cx))
    pts = [bx, cx, cy]
    # cube-like heuristic for D-H from distances to A/B/C
    for a in ANCHORS[3:]:
        da, db, dc = d("A", a), d("B", a), d("C", a)
        x = (da * da + ab * ab - db * db) / (2 * ab) if ab > 1 else 0.0
        y = (da * da + cx * cx + cy * cy - dc * dc - 2 * cx * x) / (2 * cy) if cy > 1 else 0.0
        z = math.sqrt(max(100.0, da * da - x * x - y * y))
        # split lower/upper using label heuristic
        if a in "EFGH":
            z = -z
        pts += [x, y, z]
    return np.array(pts, dtype=float)


def solve_layout(fused, anchor_sigma, robust=False, with_delays=False):
    x0 = initial_layout_from_dist(fused)
    if with_delays:
        x0 = np.r_[x0, np.zeros(8)]
    pairs = list(fused.items())
    def residual(x):
        layout = pack_layout(x[:18])
        delays = {a: 0.0 for a in ANCHORS}
        if with_delays:
            ds = x[18:26]
            delays = {a: ds[i] for i, a in enumerate(ANCHORS)}
        res = []
        for (a, b), obs in pairs:
            pred = np.linalg.norm(layout[a] - layout[b]) + delays[a] + delays[b]
            sig = max(5.0, math.sqrt(anchor_sigma[a] ** 2 + anchor_sigma[b] ** 2))
            res.append((pred - obs) / sig)
        if with_delays:
            res.extend([d / 200.0 for d in x[18:26]])
        return np.array(res)
    ans = least_squares(residual, x0, loss="huber" if robust else "linear", f_scale=2.0, max_nfev=20000)
    layout = pack_layout(ans.x[:18])
    delays = {a: 0.0 for a in ANCHORS}
    if with_delays:
        delays = {a: float(ans.x[18 + i]) for i, a in enumerate(ANCHORS)}
    raw_res = []
    for (a, b), obs in pairs:
        pred = float(np.linalg.norm(layout[a] - layout[b]) + delays[a] + delays[b])
        raw_res.append({"pair": a + b, "obs": obs, "pred": pred, "residual": pred - obs})
    return layout, delays, raw_res, rmse([r["residual"] for r in raw_res])


def solve_point(ranges: dict[str, float], layout: dict[str, np.ndarray], anchor_sigma: dict[str, float]):
    valid = [(a, float(d)) for a, d in ranges.items() if a in layout and np.isfinite(d) and d > 0]
    if len(valid) < 4:
        return None
    anchors = np.array([layout[a] for a, _ in valid])
    ds = np.array([d for _, d in valid])
    sig = np.array([max(5.0, anchor_sigma[a]) for a, _ in valid])
    x0 = anchors.mean(axis=0)
    def res(x):
        return (np.linalg.norm(anchors - x, axis=1) - ds) / sig
    ans = least_squares(res, x0, loss="huber", f_scale=2.0, max_nfev=80)
    return ans.x


def iter_sweeps(path: Path):
    rows = read_csv_dict(path)
    grouped = defaultdict(dict)
    peers = defaultdict(set)
    for r in rows:
        try:
            if int(float(r.get("valid", "0") or 0)) != 1:
                continue
            if r.get("status") not in ("", "O"):
                continue
            d = float(r.get("raw_mm") or r.get("range_mm"))
            if d <= 0:
                continue
            peer = r.get("peer_name", "")
            sweep = int(float(r.get("sweep", 0)))
            aid = int(float(r.get("anchor_id", -1)))
        except Exception:
            continue
        if 0 <= aid < 8:
            key = (peer, sweep)
            grouped[key][ANCHORS[aid]] = d
            peers[peer].add(sweep)
    return grouped


def solve_capture_positions(path: Path, layout, anchor_sigma):
    grouped = iter_sweeps(path)
    out = defaultdict(list)
    for (peer, sweep), ranges in grouped.items():
        p = solve_point(ranges, layout, anchor_sigma)
        if p is not None:
            out[peer].append((sweep, p, len(ranges)))
    return out


def pos_stats(points: list[np.ndarray]):
    arr = np.asarray(points, dtype=float)
    if arr.size == 0:
        return None
    std = arr.std(axis=0)
    return {
        "N": int(arr.shape[0]),
        "mean_x": float(arr[:, 0].mean()),
        "mean_y": float(arr[:, 1].mean()),
        "mean_z": float(arr[:, 2].mean()),
        "x_std": float(std[0]),
        "y_std": float(std[1]),
        "z_std": float(std[2]),
        "std_3d": float(np.linalg.norm(std)),
    }


def fit_circle_3d(points):
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] < 20:
        return None
    cen = pts.mean(axis=0)
    u, s, vh = np.linalg.svd(pts - cen, full_matrices=False)
    normal = vh[-1]
    e1, e2 = vh[0], vh[1]
    xy = np.column_stack(((pts - cen) @ e1, (pts - cen) @ e2))
    A = np.column_stack((2 * xy[:, 0], 2 * xy[:, 1], np.ones(len(xy))))
    b = xy[:, 0] ** 2 + xy[:, 1] ** 2
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy, c = sol
    radius = math.sqrt(max(0.0, c + cx * cx + cy * cy))
    center3 = cen + cx * e1 + cy * e2
    radial = np.sqrt((xy[:, 0] - cx) ** 2 + (xy[:, 1] - cy) ** 2) - radius
    z = (pts - center3) @ normal
    total = np.sqrt(radial ** 2 + z ** 2)
    ss_res = float(np.sum(radial ** 2))
    rr = np.sqrt((xy[:, 0] - cx) ** 2 + (xy[:, 1] - cy) ** 2)
    ss_tot = float(np.sum((rr - rr.mean()) ** 2))
    tilt = math.degrees(math.acos(min(1.0, max(0.0, abs(float(np.dot(normal, [0, 0, 1])))))))
    return {
        "N": int(len(pts)),
        "radius": float(radius),
        "radial_std": float(np.std(radial)),
        "z_plane_std": float(np.std(z)),
        "std_3d": float(np.std(total)),
        "rms_3d": rmse(total),
        "tilt_deg": float(tilt),
        "r2": float(1 - ss_res / ss_tot) if ss_tot > 1e-9 else float("nan"),
        "center": center3.tolist(),
        "normal": normal.tolist(),
    }


def fim_uncertainty(layout, anchor_sigma):
    # FIM over free gauge vars from inter-anchor residuals.
    x0 = []
    for a in ["B", "C", "D", "E", "F", "G", "H"]:
        if a == "B":
            x0.append(layout[a][0])
        elif a == "C":
            x0 += [layout[a][0], layout[a][1]]
        else:
            x0 += list(layout[a])
    x0 = np.array(x0, dtype=float)
    def unpack(x):
        l = {"A": np.zeros(3), "B": np.array([x[0], 0, 0]), "C": np.array([x[1], x[2], 0])}
        k = 3
        for a in "DEFGH":
            l[a] = x[k:k+3]
            k += 3
        return l
    eps = 1e-3
    rows = []
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i+1:]:
            sig = max(5.0, math.sqrt(anchor_sigma[a] ** 2 + anchor_sigma[b] ** 2))
            def f(x):
                l = unpack(x)
                return np.linalg.norm(l[a] - l[b]) / sig
            grad = np.zeros_like(x0)
            for j in range(len(x0)):
                dx = np.zeros_like(x0); dx[j] = eps
                grad[j] = (f(x0 + dx) - f(x0 - dx)) / (2 * eps)
            rows.append(grad)
    J = np.vstack(rows)
    F = J.T @ J + np.eye(J.shape[1]) * 1e-9
    cov = np.linalg.pinv(F)
    return float(np.linalg.cond(F)), cov


def main():
    ensure_dirs()
    log(f"DATA={DATA}")
    log(f"OUT={OUT}")
    pairs_path = DATA / "sweep1000" / "pairs_all.csv"
    if not pairs_path.exists():
        raise SystemExit(f"missing {pairs_path}")
    log("Part 1: sweep fusion")
    sweep_rows, d_v1, d_v3, anchor_sigma = fuse_pairs(pairs_path)
    write_csv(OUT / "tables" / "sweep_quality.csv", sweep_rows)
    write_csv(OUT / "tables" / "anchor_sigma.csv", [{"anchor": a, "sigma_mm": anchor_sigma[a]} for a in ANCHORS])
    old_rows = []
    old_pairs = OLD_ROOT / "sweeps" / "inter_anchor_500set_20260504_185011" / "pairs_all.csv"
    old_map = {}
    if old_pairs.exists():
        old_sweep, _, old_v3, old_sig = fuse_pairs(old_pairs)
        old_map = {r["pair"]: r for r in old_sweep}
        for r in sweep_rows:
            o = old_map.get(r["pair"])
            old_rows.append({
                "pair": r["pair"],
                "mad_this": np.nanmedian([r["mad_ab"], r["mad_ba"]]),
                "mad_old": np.nanmedian([o["mad_ab"], o["mad_ba"]]) if o else "",
                "asym_this": r["asymmetry"],
                "asym_old": o["asymmetry"] if o else "",
                "v3_this": r["v3_fused"],
                "v3_old": old_v3.get(tuple(r["pair"]), ""),
            })
        write_csv(OUT / "tables" / "sweep_vs_20260504.csv", old_rows)

    log("Part 2: anchor layout solve")
    solvers = {
        "V1": (d_v1, False, False),
        "V3-lite": (d_v3, False, False),
        "V3-full": (d_v3, True, True),
        "V4-io": (d_v3, True, True),
    }
    solver_rows = []
    layouts = {}
    for name, (dist, robust, delays) in solvers.items():
        layout, ds, res, rms = solve_layout(dist, anchor_sigma, robust=robust, with_delays=delays)
        layouts[name] = layout
        dump_json(OUT / "solves" / f"{name.replace('-', '_').lower()}_layout.json", layout_to_json(layout, ds))
        write_csv(OUT / "solves" / f"{name.replace('-', '_').lower()}_residuals.csv", res)
        solver_rows.append({"solver": name, "inter_rms": rms})
        log(f"  {name}: inter_rms={rms:.2f} mm")
    write_csv(OUT / "tables" / "solver_progression.csv", solver_rows)
    layout = layouts["V4-io"]
    cond, cov = fim_uncertainty(layout, anchor_sigma)
    fim_rows = []
    # approximate per-anchor uncertainty from available gauge variables.
    fim_rows.append({"anchor": "A", "sigma_x": 0, "sigma_y": 0, "sigma_z": 0, "sigma_3d": 0, "sigma_d": anchor_sigma["A"]})
    diag = np.sqrt(np.maximum(np.diag(cov), 0))
    idx = {"B": [0], "C": [1, 2]}
    k = 3
    for a in "DEFGH":
        idx[a] = [k, k+1, k+2]; k += 3
    for a in "BCDEFGH":
        vals = [0, 0, 0]
        if a == "B":
            vals[0] = diag[idx[a][0]]
        elif a == "C":
            vals[0], vals[1] = diag[idx[a][0]], diag[idx[a][1]]
        else:
            vals = [diag[j] for j in idx[a]]
        fim_rows.append({"anchor": a, "sigma_x": vals[0], "sigma_y": vals[1], "sigma_z": vals[2], "sigma_3d": float(np.linalg.norm(vals)), "sigma_d": anchor_sigma[a]})
    write_csv(OUT / "tables" / "v5_fim_uncertainty.csv", fim_rows)
    dump_json(OUT / "reports" / "v5_fim_v4_io.json", {"condition_number": cond, "rows": fim_rows})

    log("Part 3: static positioning")
    static_dirs = latest_id_dirs(DATA / "Static_Test", "ID")
    static_rows = []
    static_positions = {}
    for idn in [f"ID{i:02d}" for i in range(1, 25)]:
        d = static_dirs.get(idn)
        group, height, facing = STATIC_META.get(idn, ("unknown", "unknown", "unknown"))
        if not d:
            static_rows.append({"ID": idn, "group": group, "height": height, "facing": facing, "status": "not collected"})
            continue
        pos_by_peer = solve_capture_positions(d / "tr_all.csv", layout, anchor_sigma)
        pts = [p for peer in pos_by_peer.values() for _, p, _ in peer]
        st = pos_stats(pts)
        if not st:
            static_rows.append({"ID": idn, "group": group, "height": height, "facing": facing, "status": "insufficient"})
            continue
        static_positions[idn] = np.asarray(pts)
        row = {"ID": idn, "path": str(d), "group": group, "height": height, "facing": facing, "status": "ok", **st}
        static_rows.append(row)
    write_csv(OUT / "tables" / "static_positioning.csv", static_rows)

    spatial_rows = []
    for g in ["edge_low", "edge_mid", "edge_high", "center_mid", "center_low", "center_high"]:
        vals = [float(r["std_3d"]) for r in static_rows if r.get("group") == g and r.get("status") == "ok"]
        ids = [r["ID"] for r in static_rows if r.get("group") == g and r.get("status") == "ok"]
        if vals:
            spatial_rows.append({"group": g, "n_captures": len(vals), "std_3d_median": float(np.median(vals)), "best": ids[int(np.argmin(vals))], "worst": ids[int(np.argmax(vals))]})
    write_csv(OUT / "tables" / "static_spatial_summary.csv", spatial_rows)

    orient_rows = []
    for height, ids in {"low": ["ID17", "ID18", "ID19", "ID20"], "mid": ["ID13", "ID14", "ID15", "ID16"], "high": ["ID21", "ID22", "ID23", "ID24"]}.items():
        row = {"height": height}
        vals = []
        for idn in ids:
            facing = STATIC_META[idn][2]
            val = next((r.get("std_3d") for r in static_rows if r.get("ID") == idn and r.get("status") == "ok"), "")
            row[facing] = val
            if val != "":
                vals.append(float(val))
        row["spread"] = max(vals) - min(vals) if vals else ""
        orient_rows.append(row)
    write_csv(OUT / "tables" / "orientation_effect.csv", orient_rows)

    log("Part 4: roto circle fit")
    roto_dirs = latest_id_dirs(DATA / "Roto_Test", "ID")
    roto_rows = []
    for idn in [f"ID{i:02d}" for i in range(25, 42)]:
        d = roto_dirs.get(idn)
        tilt, facing = ROTO_META.get(idn, ("unknown", "unknown"))
        if not d:
            roto_rows.append({"ID": idn, "tilt": tilt, "facing": facing, "status": "not collected"})
            continue
        pos_by_peer = solve_capture_positions(d / "tr_all.csv", layout, anchor_sigma)
        for peer, vals in pos_by_peer.items():
            pts = [p for _, p, _ in vals]
            fit = fit_circle_3d(pts)
            if fit is None:
                roto_rows.append({"ID": idn, "tilt": tilt, "facing": facing, "tag": peer, "status": "insufficient", "N": len(pts)})
                continue
            expected = ROTO_R_INNER if peer == "BS2DCE" else ROTO_R_OUTER
            roto_rows.append({
                "ID": idn, "tilt": tilt, "facing": facing, "tag": peer, "status": "ok",
                "N": fit["N"], "radius": fit["radius"], "expected_radius": expected,
                "radius_bias": fit["radius"] - expected,
                "radial_std": fit["radial_std"], "z_plane_std": fit["z_plane_std"],
                "std_3d": fit["std_3d"], "rms_3d": fit["rms_3d"], "tilt_deg": fit["tilt_deg"], "r2": fit["r2"],
            })
    write_csv(OUT / "tables" / "roto_dynamic_error.csv", roto_rows)
    tilt_rows = []
    for tilt in ["planar", "small", "mid", "high", "vertical"]:
        rows = [r for r in roto_rows if r.get("tilt") == tilt and r.get("status") == "ok"]
        if rows:
            inner = [r for r in rows if r["tag"] == "BS2DCE"]
            outer = [r for r in rows if r["tag"] == "BSDC91"]
            drs = []
            for idn in sorted(set(r["ID"] for r in rows)):
                ii = next((r for r in inner if r["ID"] == idn), None)
                oo = next((r for r in outer if r["ID"] == idn), None)
                if ii and oo:
                    drs.append(float(oo["radius"]) - float(ii["radius"]))
            tilt_rows.append({
                "tilt_level": tilt, "n_captures": len(set(r["ID"] for r in rows)),
                "std_3d_median": float(np.median([float(r["std_3d"]) for r in rows])),
                "radius_mean": float(np.mean([float(r["radius"]) for r in rows])),
                "delta_r_mean": float(np.mean(drs)) if drs else "",
                "tilt_deg_mean": float(np.mean([float(r["tilt_deg"]) for r in rows])),
            })
    write_csv(OUT / "tables" / "roto_tilt_ablation.csv", tilt_rows)

    log("Part 5: wand rigid body")
    wand_dirs = latest_w_dirs(DATA / "Wand_Test")
    wand_rows = []
    wand_orient = []
    for wid in [f"W{i:02d}" for i in range(1, 6)]:
        d = wand_dirs.get(wid)
        if not d:
            for pair, gt in WAND_DISTANCES.items():
                wand_rows.append({"capture": wid, "pair": "-".join(pair), "gt_dist": gt, "status": "not collected"})
            continue
        pos_by_peer = solve_capture_positions(d / "tr_all.csv", layout, anchor_sigma)
        by_sweep = defaultdict(dict)
        for peer, vals in pos_by_peer.items():
            for sweep, p, _ in vals:
                by_sweep[sweep][peer] = p
        for pair, gt in WAND_DISTANCES.items():
            vals = []
            for peers in by_sweep.values():
                if pair[0] in peers and pair[1] in peers:
                    vals.append(float(np.linalg.norm(peers[pair[0]] - peers[pair[1]])))
            if vals:
                med = float(np.median(vals))
                wand_rows.append({"capture": wid, "pair": "-".join(pair), "gt_dist": gt, "measured_median": med, "bias": med - gt, "std": float(np.std(vals)), "N": len(vals), "status": "ok", "note": WAND_META.get(wid, "")})
            else:
                wand_rows.append({"capture": wid, "pair": "-".join(pair), "gt_dist": gt, "status": "insufficient"})
        if wid in ["W01", "W02", "W03", "W04"]:
            wand_orient.append(wid)
    write_csv(OUT / "tables" / "wand_rigid_body.csv", wand_rows)
    inv_rows = []
    for pair in WAND_DISTANCES:
        row = {"pair": "-".join(pair)}
        vals = []
        for wid in ["W01", "W02", "W03", "W04"]:
            val = next((r.get("measured_median") for r in wand_rows if r.get("capture") == wid and r.get("pair") == "-".join(pair) and r.get("status") == "ok"), "")
            row[wid] = val
            if val != "":
                vals.append(float(val))
        row["spread"] = max(vals) - min(vals) if vals else ""
        inv_rows.append(row)
    write_csv(OUT / "tables" / "wand_orientation_invariance.csv", inv_rows)

    log("Figures")
    # fig01 sweep MAD comparison
    try:
        pairs = [r["pair"] for r in sweep_rows]
        mad_this = [np.nanmedian([r["mad_ab"], r["mad_ba"]]) for r in sweep_rows]
        mad_old = [float(next((o["mad_old"] for o in old_rows if o["pair"] == p and o["mad_old"] != ""), np.nan)) for p in pairs]
        x = np.arange(len(pairs))
        plt.figure(figsize=(12, 5))
        plt.bar(x - 0.2, mad_this, 0.4, label="20260513")
        if old_rows:
            plt.bar(x + 0.2, mad_old, 0.4, label="20260504")
        plt.xticks(x, pairs, rotation=60)
        plt.ylabel("MAD mm")
        plt.legend()
        plt.tight_layout()
        plt.savefig(OUT / "figures" / "fig01_sweep_quality_comparison.png", dpi=300)
        plt.close()
    except Exception as e:
        log(f"fig01 failed: {e}")

    try:
        ok = [r for r in static_rows if r.get("status") == "ok"]
        plt.figure(figsize=(7, 6))
        xs = [float(r["mean_x"]) for r in ok]; ys = [float(r["mean_y"]) for r in ok]; cs = [float(r["std_3d"]) for r in ok]
        sc = plt.scatter(xs, ys, c=cs, s=np.array(cs) * 2 + 20, cmap="viridis")
        for r in ok:
            plt.text(float(r["mean_x"]), float(r["mean_y"]), r["ID"], fontsize=7)
        plt.colorbar(sc, label="3D std mm")
        plt.axis("equal"); plt.tight_layout()
        plt.savefig(OUT / "figures" / "fig02_static_spatial_heatmap.png", dpi=300)
        plt.close()
    except Exception as e:
        log(f"fig02 failed: {e}")

    try:
        heights = ["low", "mid", "high"]; faces = ["ABEF", "BCGF", "CDHG", "ADHE"]
        x = np.arange(len(heights)); width = 0.2
        plt.figure(figsize=(8, 5))
        for j, face in enumerate(faces):
            vals = [float(next((r[face] for r in orient_rows if r["height"] == h and r.get(face) != ""), np.nan)) for h in heights]
            plt.bar(x + (j - 1.5) * width, vals, width, label=face)
        plt.xticks(x, heights); plt.ylabel("3D std mm"); plt.legend(); plt.tight_layout()
        plt.savefig(OUT / "figures" / "fig03_antenna_orientation_effect.png", dpi=300)
        plt.close()
    except Exception as e:
        log(f"fig03 failed: {e}")

    try:
        rows = [r for r in roto_rows if r.get("status") == "ok"]
        plt.figure(figsize=(7, 5))
        for tag in ["BS2DCE", "BSDC91"]:
            rr = sorted([r for r in rows if r["tag"] == tag], key=lambda r: (float(r["tilt_deg"]), r["ID"]))
            plt.plot([float(r["tilt_deg"]) for r in rr], [float(r["std_3d"]) for r in rr], "o-", label=tag)
        plt.xlabel("tilt angle deg"); plt.ylabel("circle-fit 3D residual std mm"); plt.legend(); plt.tight_layout()
        plt.savefig(OUT / "figures" / "fig04_roto_tilt_ablation.png", dpi=300)
        plt.close()
        plt.figure(figsize=(7, 5))
        for tag in ["BS2DCE", "BSDC91"]:
            rr = [r for r in rows if r["tag"] == tag]
            plt.scatter([float(r["tilt_deg"]) for r in rr], [float(r["radius"]) for r in rr], label=tag)
        plt.xlabel("tilt angle deg"); plt.ylabel("fitted radius mm"); plt.legend(); plt.tight_layout()
        plt.savefig(OUT / "figures" / "fig05_roto_radius_vs_tilt.png", dpi=300)
        plt.close()
    except Exception as e:
        log(f"roto figs failed: {e}")

    try:
        # fig06 W05 distances by sweep index
        d = wand_dirs.get("W05")
        if d:
            pos_by_peer = solve_capture_positions(d / "tr_all.csv", layout, anchor_sigma)
            by_sweep = defaultdict(dict)
            for peer, vals in pos_by_peer.items():
                for sweep, p, _ in vals:
                    by_sweep[sweep][peer] = p
            plt.figure(figsize=(10, 5))
            for pair, gt in WAND_DISTANCES.items():
                xs, ys = [], []
                for sweep in sorted(by_sweep):
                    peers = by_sweep[sweep]
                    if pair[0] in peers and pair[1] in peers:
                        xs.append(sweep)
                        ys.append(float(np.linalg.norm(peers[pair[0]] - peers[pair[1]])))
                if xs:
                    plt.plot(xs, ys, ".", ms=1, label="-".join(pair))
                    plt.axhline(gt, lw=1, ls="--")
            plt.ylabel("inter-tag distance mm"); plt.xlabel("sweep"); plt.legend(); plt.tight_layout()
            plt.savefig(OUT / "figures" / "fig06_wand_distance_timeseries.png", dpi=300)
            plt.close()
    except Exception as e:
        log(f"fig06 failed: {e}")

    try:
        methods = ["Sweep only (V4-io)", "Wand only", "Sweep + Wand"]
        vals = [float(np.median([float(r["std_3d"]) for r in static_rows if r.get("group") in ("center_mid", "center_low", "center_high") and r.get("status") == "ok"])), np.nan, np.nan]
        plt.figure(figsize=(6, 4)); plt.bar(methods, vals); plt.xticks(rotation=15); plt.ylabel("center 3D std mm"); plt.tight_layout()
        plt.savefig(OUT / "figures" / "fig08_calibration_comparison_bar.png", dpi=300); plt.close()
        plt.figure(figsize=(6, 4)); plt.bar([r["solver"] for r in solver_rows], [float(r["inter_rms"]) for r in solver_rows]); plt.ylabel("inter-anchor RMS mm"); plt.tight_layout()
        plt.savefig(OUT / "figures" / "fig09_v1_v4_progression.png", dpi=300); plt.close()
    except Exception as e:
        log(f"summary figs failed: {e}")

    log("Report")
    report = []
    report.append("# AutoPos Outdoor 20260513 Full Analysis\n")
    report.append(f"Data root: `{DATA}`\n\nOutput: `{OUT}`\n")
    report.append("## Key Findings\n")
    d_h_pairs = [r for r in sweep_rows if "D" in r["pair"] or "H" in r["pair"]]
    report.append(f"1. Sweep quality: {len(sweep_rows)} inter-anchor pairs loaded. Median pair MAD is {np.nanmedian([np.nanmedian([r['mad_ab'], r['mad_ba']]) for r in sweep_rows]):.2f} mm; D/H-related median MAD is {np.nanmedian([np.nanmedian([r['mad_ab'], r['mad_ba']]) for r in d_h_pairs]):.2f} mm.\n")
    report.append(f"2. V1-V4 progression: V4-io inter-anchor RMS is {next(r['inter_rms'] for r in solver_rows if r['solver']=='V4-io'):.2f} mm; all solver JSONs are under `solves/`.\n")
    center_vals = [float(r["std_3d"]) for r in static_rows if r.get("group") in ("center_mid", "center_low", "center_high") and r.get("status") == "ok"]
    report.append(f"3. Static positioning: collected {sum(1 for r in static_rows if r.get('status')=='ok')}/24 static captures; center median 3D std is {np.median(center_vals):.2f} mm.\n")
    spreads = [float(r["spread"]) for r in orient_rows if r.get("spread") != ""]
    report.append(f"4. Antenna orientation: max height-level orientation spread is {max(spreads):.2f} mm. See `tables/orientation_effect.csv`.\n")
    roto_ok = [r for r in roto_rows if r.get("status") == "ok"]
    report.append(f"5. Roto tilt ablation: {len(set(r['ID'] for r in roto_ok))} captures analyzed; median circle-fit 3D std is {np.median([float(r['std_3d']) for r in roto_ok]):.2f} mm.\n")
    wand_ok = [r for r in wand_rows if r.get("status") == "ok"]
    report.append(f"6. Wand rigid body: {len(wand_ok)} pair summaries generated. W05 free move is analyzed, but should not be used as strong same-frame rigid constraint because TDMA tag positions are not simultaneous.\n")
    report.append("7. Wand-only calibration: marked experimental/not solved in this single-pass script; table reserves the comparison slot as NaN.\n")
    report.append("8. Sweep+Wand fusion: marked experimental/not solved in this single-pass script; current recommended baseline remains sweep-only V4-io plus validation captures.\n")
    report.append("9. 20260504 comparison: loaded if `outdoor_v4_20260504/sweeps/inter_anchor_500set_20260504_185011/pairs_all.csv` exists; see `tables/sweep_vs_20260504.csv`.\n")
    report.append("10. New finding: in Roto vertical/high captures, BS2DCE usually has much higher ge8/circle robustness than BSDC91; this is visible in raw capture summaries and circle fit tables.\n")
    report.append("\n## Tables\n")
    for p in sorted((OUT / "tables").glob("*.csv")):
        report.append(f"- `{p.relative_to(OUT)}`\n")
    report.append("\n## Figures\n")
    for p in sorted((OUT / "figures").glob("*.png")):
        report.append(f"- `{p.relative_to(OUT)}`\n")
    (OUT / "reports" / "full_analysis_report.md").write_text("".join(report), encoding="utf-8")

    # sweep quality markdown requested separately
    sq = ["# Sweep Quality\n\n", f"Source: `{pairs_path}`\n\n", "See `../tables/sweep_quality.csv`, `../tables/anchor_sigma.csv`, and `../tables/sweep_vs_20260504.csv`.\n"]
    (OUT / "reports" / "sweep_quality.md").write_text("".join(sq), encoding="utf-8")
    log("DONE")
    log(str(OUT / "reports" / "full_analysis_report.md"))


if __name__ == "__main__":
    main()
