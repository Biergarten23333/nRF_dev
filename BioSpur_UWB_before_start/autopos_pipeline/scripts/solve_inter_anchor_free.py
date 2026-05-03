#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median

import numpy as np
from scipy.optimize import least_squares


ANCHORS = "ABCDEFGH"


def load_layout(path: Path) -> np.ndarray:
    raw = json.loads(path.read_text(encoding="utf-8"))
    xyz = np.zeros((8, 3), dtype=float)
    if "anchors" in raw:
        for ent in raw["anchors"]:
            idx = ANCHORS.index(str(ent["label"]).upper()) if "label" in ent else int(ent["id"])
            xyz[idx] = [float(ent["x_mm"]), float(ent["y_mm"]), float(ent["z_mm"])]
    elif "layout" in raw:
        for k, v in raw["layout"].items():
            xyz[int(k)] = [float(v[0]), float(v[1]), float(v[2])]
    else:
        raise KeyError("layout JSON must contain anchors[] or layout{}")
    return xyz


def load_pairs(path: Path) -> tuple[list[dict], np.ndarray]:
    groups: dict[tuple[int, int], list[float]] = defaultdict(list)
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            a = row["a"].strip().upper()
            b = row["b"].strip().upper()
            if a not in ANCHORS or b not in ANCHORS or a == b:
                continue
            d = float(row.get("dist_mm") or row.get("distance_mm") or row.get("raw_mm") or 0)
            q = float(row.get("quality_percent") or row.get("quality") or 100)
            if d <= 0 or q <= 0:
                continue
            i, j = sorted((ANCHORS.index(a), ANCHORS.index(b)))
            groups[(i, j)].append(d)
    pairs = []
    dist = np.zeros((8, 8), dtype=float)
    for (i, j), vals in sorted(groups.items()):
        vals = sorted(vals)
        med = float(median(vals))
        std = float(np.std(vals))
        pairs.append(
            {
                "i": i,
                "j": j,
                "pair": f"{ANCHORS[i]}-{ANCHORS[j]}",
                "range_mm": med,
                "std_mm": std,
                "n": len(vals),
                "min_mm": float(vals[0]),
                "max_mm": float(vals[-1]),
            }
        )
        dist[i, j] = dist[j, i] = med
    return pairs, dist


def mds_init(dist: np.ndarray) -> np.ndarray:
    d2 = dist * dist
    j = np.eye(8) - np.ones((8, 8)) / 8.0
    b = -0.5 * j @ d2 @ j
    vals, vecs = np.linalg.eigh(b)
    order = np.argsort(vals)[::-1]
    vals = vals[order]
    vecs = vecs[:, order]
    xyz = vecs[:, :3] * np.sqrt(np.maximum(vals[:3], 0.0))
    return gauge_align(xyz)


def gauge_align(xyz: np.ndarray) -> np.ndarray:
    out = xyz.copy()
    out -= out[0]
    bx = out[1]
    bx_norm = np.linalg.norm(bx)
    if bx_norm < 1e-9:
        return out
    ex = bx / bx_norm
    c = out[2]
    c_perp = c - np.dot(c, ex) * ex
    if np.linalg.norm(c_perp) < 1e-9:
        ey = np.array([0.0, 1.0, 0.0])
    else:
        ey = c_perp / np.linalg.norm(c_perp)
    ez = np.cross(ex, ey)
    r = np.vstack([ex, ey, ez]).T
    out = out @ r
    if np.mean(out[4:8, 2]) < np.mean(out[:4, 2]):
        out[:, 2] *= -1.0
    out[0] = 0.0
    out[1, 1:] = 0.0
    out[2, 2] = 0.0
    return out


def pack(xyz: np.ndarray) -> np.ndarray:
    vals = []
    vals.append(float(xyz[1, 0]))
    vals.extend(xyz[2, 0:2].tolist())
    vals.extend(xyz[3, 0:2].tolist())
    for aid in range(4, 8):
        vals.extend(xyz[aid].tolist())
    return np.asarray(vals, dtype=float)


def unpack(x: np.ndarray) -> np.ndarray:
    xyz = np.zeros((8, 3), dtype=float)
    k = 0
    xyz[1] = [x[k], 0.0, 0.0]
    k += 1
    xyz[2] = [x[k], x[k + 1], 0.0]
    k += 2
    xyz[3] = [x[k], x[k + 1], 0.0]
    k += 2
    for aid in range(4, 8):
        xyz[aid] = x[k : k + 3]
        k += 3
    return xyz


def solve(pairs: list[dict], init: np.ndarray, loss: str, sigma_mm: float, f_scale: float) -> tuple[np.ndarray, least_squares]:
    def residual(x: np.ndarray) -> np.ndarray:
        xyz = unpack(x)
        res = []
        for p in pairs:
            pred = float(np.linalg.norm(xyz[p["i"]] - xyz[p["j"]]))
            res.append((pred - float(p["range_mm"])) / sigma_mm)
        return np.asarray(res, dtype=float)

    sol = least_squares(residual, pack(gauge_align(init)), loss=loss, f_scale=f_scale, max_nfev=1000)
    return unpack(sol.x), sol


def stats(xyz: np.ndarray, pairs: list[dict]) -> dict:
    errs = []
    for p in pairs:
        pred = float(np.linalg.norm(xyz[p["i"]] - xyz[p["j"]]))
        err = pred - float(p["range_mm"])
        row = dict(p)
        row["pred_mm"] = pred
        row["err_mm"] = err
        row["abs_err_mm"] = abs(err)
        errs.append(row)
    arr = np.asarray([e["err_mm"] for e in errs], dtype=float)
    errs.sort(key=lambda r: r["abs_err_mm"], reverse=True)
    return {
        "rms_mm": float(math.sqrt(float(np.mean(arr * arr)))),
        "median_abs_mm": float(median([abs(float(v)) for v in arr])),
        "max_abs_mm": float(max(abs(float(v)) for v in arr)),
        "top_errors": errs[:12],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Inter-anchor-only free geometry solve.")
    ap.add_argument("--pairs-csv", required=True)
    ap.add_argument("--init-layout", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--sigma-mm", type=float, default=30.0)
    ap.add_argument("--f-scale", type=float, default=2.0, help="Robust loss transition in normalized residual units")
    ap.add_argument("--losses", default="linear,soft_l1", help="Comma-separated scipy least_squares losses")
    args = ap.parse_args()

    pairs, dist = load_pairs(Path(args.pairs_csv))
    apos = load_layout(Path(args.init_layout))
    mds = mds_init(dist)
    results = {}
    losses = [x.strip() for x in args.losses.split(",") if x.strip()]
    for name, init in [("apos_init", apos), ("mds_init", mds)]:
        for loss in losses:
            xyz, sol = solve(pairs, init, loss, args.sigma_mm, args.f_scale)
            key = f"{name}_{loss}"
            results[key] = {
                "anchors": [
                    {"id": i, "label": ANCHORS[i], "x_mm": float(xyz[i, 0]), "y_mm": float(xyz[i, 1]), "z_mm": float(xyz[i, 2])}
                    for i in range(8)
                ],
                "stats": stats(xyz, pairs),
                "optimizer": {"success": bool(sol.success), "cost": float(sol.cost), "nfev": int(sol.nfev), "message": sol.message},
            }
    payload = {
        "schema": "inter_anchor_free_solve_v1",
        "pairs_csv": str(Path(args.pairs_csv)),
        "init_layout": str(Path(args.init_layout)),
        "pair_count": len(pairs),
        "sigma_mm": args.sigma_mm,
        "f_scale": args.f_scale,
        "results": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v["stats"] for k, v in results.items()}, indent=2))
    print(f"[ok] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
