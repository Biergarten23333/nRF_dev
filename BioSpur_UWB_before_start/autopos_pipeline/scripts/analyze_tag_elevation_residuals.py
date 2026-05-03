#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import least_squares


ANCHORS = "ABCDEFGH"


def load_layout(path: Path) -> np.ndarray:
    raw = json.loads(path.read_text(encoding="utf-8"))
    coords = np.zeros((8, 3), dtype=float)
    for ent in raw["anchors"]:
        idx = ANCHORS.index(str(ent["label"]).upper()) if "label" in ent else int(ent["id"])
        coords[idx] = [float(ent["x_mm"]), float(ent["y_mm"]), float(ent["z_mm"])]
    return coords


def load_tag_ranges(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("tag_anchor_ranges") or []
    out = []
    for r in rows:
        try:
            out.append(
                {
                    "tag": str(r["tag"]),
                    "anchor": int(r["anchor"]),
                    "range_mm": float(r["range_mm"]),
                    "quality": float(r.get("quality", 100.0)),
                    "sweep": str(r.get("sweep", "")),
                }
            )
        except Exception:
            continue
    return out


def robust_initial_position(anchor_xyz: np.ndarray, rows: list[dict[str, Any]]) -> np.ndarray:
    by_anchor: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        by_anchor[int(r["anchor"])].append(float(r["range_mm"]))
    weights = []
    points = []
    for aid, vals in by_anchor.items():
        rng = max(float(np.median(vals)), 100.0)
        weights.append(1.0 / rng)
        points.append(anchor_xyz[aid])
    if not points:
        p = np.mean(anchor_xyz, axis=0)
        p[2] = max(300.0, min(1500.0, p[2]))
        return p
    return np.average(np.asarray(points), axis=0, weights=np.asarray(weights))


def fit_stationary_tag(anchor_xyz: np.ndarray, rows: list[dict[str, Any]]) -> tuple[np.ndarray, float, Any]:
    x0_pos = robust_initial_position(anchor_xyz, rows)
    x0 = np.array([x0_pos[0], x0_pos[1], x0_pos[2], 0.0], dtype=float)

    def fun(x: np.ndarray) -> np.ndarray:
        pos = x[:3]
        d_tag = float(x[3])
        res = []
        for r in rows:
            aid = int(r["anchor"])
            pred = float(np.linalg.norm(pos - anchor_xyz[aid]) + d_tag)
            res.append(pred - float(r["range_mm"]))
        return np.asarray(res, dtype=float)

    result = least_squares(fun, x0, loss="huber", f_scale=50.0, max_nfev=300)
    return result.x[:3], float(result.x[3]), result


def summarize(vals: list[float]) -> dict[str, float]:
    arr = np.asarray(vals, dtype=float)
    if arr.size == 0:
        return {"n": 0}
    return {
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "std": float(np.std(arr)),
        "rms": float(np.sqrt(np.mean(arr * arr))),
        "p05": float(np.percentile(arr, 5)),
        "p95": float(np.percentile(arr, 95)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyze stationary tag-anchor residuals versus elevation angle.")
    ap.add_argument("--data", required=True, help="V4 data JSON containing tag_anchor_ranges")
    ap.add_argument("--layout", required=True, help="Anchor layout JSON to hold fixed")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-csv", required=True)
    args = ap.parse_args()

    data_path = Path(args.data)
    layout_path = Path(args.layout)
    anchor_xyz = load_layout(layout_path)
    rows = load_tag_ranges(data_path)
    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_tag[str(r["tag"])].append(r)

    detail_rows = []
    tags_payload = []
    all_pairs = []
    for tag in sorted(by_tag):
        tag_rows = by_tag[tag]
        pos, d_tag, result = fit_stationary_tag(anchor_xyz, tag_rows)
        residuals_by_anchor: dict[int, list[float]] = defaultdict(list)
        for r in tag_rows:
            aid = int(r["anchor"])
            vec = anchor_xyz[aid] - pos
            dxy = float(np.linalg.norm(vec[:2]))
            dz = float(vec[2])
            elev_deg = float(math.degrees(math.atan2(dz, dxy)))
            pred = float(np.linalg.norm(vec) + d_tag)
            resid = pred - float(r["range_mm"])
            residuals_by_anchor[aid].append(resid)
            detail_rows.append(
                {
                    "tag": tag,
                    "anchor": ANCHORS[aid],
                    "anchor_id": aid,
                    "elevation_deg": elev_deg,
                    "residual_mm": resid,
                    "range_mm": float(r["range_mm"]),
                }
            )
            all_pairs.append((elev_deg, resid))

        anchor_summary = []
        for aid in range(8):
            vals = residuals_by_anchor.get(aid, [])
            vec = anchor_xyz[aid] - pos
            elev_deg = float(math.degrees(math.atan2(float(vec[2]), float(np.linalg.norm(vec[:2])))))
            entry = {
                "anchor": ANCHORS[aid],
                "anchor_id": aid,
                "elevation_deg": elev_deg,
                **summarize(vals),
            }
            anchor_summary.append(entry)

        tag_pairs = [(r["elevation_deg"], r["residual_mm"]) for r in detail_rows if r["tag"] == tag]
        corr = None
        if len(tag_pairs) > 2:
            elev = np.asarray([p[0] for p in tag_pairs], dtype=float)
            resid = np.asarray([p[1] for p in tag_pairs], dtype=float)
            if np.std(elev) > 1e-9 and np.std(resid) > 1e-9:
                corr = float(np.corrcoef(elev, resid)[0, 1])

        tags_payload.append(
            {
                "tag": tag,
                "n_obs": len(tag_rows),
                "position_mm": [float(x) for x in pos],
                "d_tag_mm": d_tag,
                "fit_rms_mm": float(np.sqrt(np.mean(result.fun * result.fun))),
                "fit_cost": float(result.cost),
                "fit_success": bool(result.success),
                "corr_residual_vs_elevation": corr,
                "per_anchor": anchor_summary,
            }
        )

    overall_corr = None
    if len(all_pairs) > 2:
        elev = np.asarray([p[0] for p in all_pairs], dtype=float)
        resid = np.asarray([p[1] for p in all_pairs], dtype=float)
        if np.std(elev) > 1e-9 and np.std(resid) > 1e-9:
            overall_corr = float(np.corrcoef(elev, resid)[0, 1])

    out = {
        "schema": "tag_elevation_residual_analysis_v1",
        "data": str(data_path),
        "layout": str(layout_path),
        "model": "fixed anchor layout; per-stationary-tag position + scalar d_tag fitted; residual = geom + d_tag - range",
        "overall_corr_residual_vs_elevation": overall_corr,
        "tags": tags_payload,
    }
    Path(args.out_json).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    with Path(args.out_csv).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["tag", "anchor", "anchor_id", "elevation_deg", "residual_mm", "range_mm"])
        w.writeheader()
        w.writerows(detail_rows)
    print(json.dumps({"overall_corr": overall_corr, "tags": tags_payload}, indent=2)[:6000])
    print(f"[ok] wrote {args.out_json}")
    print(f"[ok] wrote {args.out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
