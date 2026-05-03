#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
from scipy.optimize import least_squares


ANCHORS = "ABCDEFGH"


def load_anchor_layout(path: Path) -> np.ndarray:
    raw = json.loads(path.read_text(encoding="utf-8"))
    coords = np.zeros((8, 3), dtype=float)
    if "anchors" in raw:
        for ent in raw["anchors"]:
            idx = ANCHORS.index(str(ent["label"]).upper()) if "label" in ent else int(ent["id"])
            coords[idx] = [float(ent["x_mm"]), float(ent["y_mm"]), float(ent["z_mm"])]
    elif "layout" in raw:
        for key, val in raw["layout"].items():
            coords[int(key)] = [float(val[0]), float(val[1]), float(val[2])]
    else:
        raise KeyError(f"{path} has neither 'anchors' nor APOS 'layout'")
    return coords


def load_anchor_delays(path: Path | None) -> np.ndarray:
    delays = np.zeros(8, dtype=float)
    if path is None:
        return delays
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "anchors" in raw:
        for ent in raw["anchors"]:
            idx = ANCHORS.index(str(ent["label"]).upper()) if "label" in ent else int(ent["id"])
            delays[idx] = float(ent.get("delay_mm") or 0.0)
    elif "anchor_delays_mm" in raw:
        vals = raw["anchor_delays_mm"]
        if isinstance(vals, dict):
            for key, val in vals.items():
                idx = ANCHORS.index(key.upper()) if str(key).upper() in ANCHORS else int(key)
                delays[idx] = float(val)
        else:
            for idx, val in enumerate(vals[:8]):
                delays[idx] = float(val)
    return delays


def find_range_csv(path: Path) -> Path:
    if path.is_file():
        return path
    if (path / "tr_all.csv").exists():
        return path / "tr_all.csv"
    if (path / "cr_all.csv").exists():
        return path / "cr_all.csv"
    matches = sorted(path.rglob("tr_all.csv")) or sorted(path.rglob("cr_all.csv"))
    if not matches:
        raise FileNotFoundError(f"no tr_all.csv or cr_all.csv under {path}")
    return matches[0]


def parse_anchor_id(value: str) -> int | None:
    s = str(value or "").strip().upper()
    if not s:
        return None
    if s in ANCHORS:
        return ANCHORS.index(s)
    try:
        i = int(s)
    except ValueError:
        return None
    return i if 0 <= i < 8 else None


def read_ranges(path: Path, min_quality: float) -> dict[str, list[dict[str, Any]]]:
    csv_path = find_range_csv(path)
    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tag = (row.get("peer_name") or row.get("tag") or row.get("tag_id") or "").strip()
            if not tag:
                continue
            aid = parse_anchor_id(row.get("anchor_id") or row.get("anchor") or row.get("peer") or "")
            if aid is None:
                continue
            status = (row.get("status") or "").strip().lower()
            valid = (row.get("valid") or "").strip().lower()
            ok = status in {"o", "ok", "success", "valid", "1", ""} or valid in {"1", "true", "yes"}
            if not ok:
                continue
            try:
                rng = float(row.get("range_mm") or row.get("filt_mm") or row.get("raw_mm") or row.get("distance_mm") or "")
                q = float(row.get("quality_percent") or row.get("quality") or 100.0)
            except ValueError:
                continue
            if not math.isfinite(rng) or rng <= 0 or q < min_quality:
                continue
            by_tag[tag].append(
                {
                    "anchor": aid,
                    "range_mm": rng,
                    "quality": q,
                    "sweep": row.get("sweep") or "",
                }
            )
    return by_tag


def median_by_anchor(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[int, list[float]] = defaultdict(list)
    qs: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        aid = int(r["anchor"])
        groups[aid].append(float(r["range_mm"]))
        qs[aid].append(float(r.get("quality") or 100.0))
    out = []
    for aid, vals in sorted(groups.items()):
        out.append(
            {
                "anchor": aid,
                "range_mm": float(median(vals)),
                "quality": float(median(qs[aid])),
                "n": len(vals),
            }
        )
    return out


def solve_position_only(anchor_xyz: np.ndarray, anchor_delay: np.ndarray, obs: list[dict[str, Any]]) -> np.ndarray:
    centroid = np.mean(anchor_xyz, axis=0)
    centroid[2] = max(200.0, min(1600.0, centroid[2]))

    def residual_pos(p: np.ndarray) -> np.ndarray:
        res = []
        for r in obs:
            aid = int(r["anchor"])
            pred = float(np.linalg.norm(p - anchor_xyz[aid]) + anchor_delay[aid])
            res.append(pred - float(r["range_mm"]))
        return np.asarray(res, dtype=float)

    sol = least_squares(residual_pos, centroid, loss="soft_l1", f_scale=100.0, max_nfev=500)
    return sol.x.astype(float)


def solve_tag_delay(
    anchor_xyz: np.ndarray,
    anchor_delay: np.ndarray,
    obs: list[dict[str, Any]],
    sigma_mm: float,
    x0_pos: np.ndarray | None = None,
) -> dict[str, Any]:
    if len({int(r["anchor"]) for r in obs}) < 4:
        raise ValueError("need at least 4 anchors to solve position+d_tag")
    if x0_pos is None:
        x0_pos = solve_position_only(anchor_xyz, anchor_delay, obs)
    x0 = np.array([x0_pos[0], x0_pos[1], x0_pos[2], 0.0], dtype=float)

    def residual(params: np.ndarray) -> np.ndarray:
        p = params[:3]
        d_tag = float(params[3])
        res = []
        for r in obs:
            aid = int(r["anchor"])
            pred = float(np.linalg.norm(p - anchor_xyz[aid]) + anchor_delay[aid] + d_tag)
            res.append((pred - float(r["range_mm"])) / sigma_mm)
        return np.asarray(res, dtype=float)

    sol = least_squares(residual, x0, loss="huber", f_scale=1.0, max_nfev=1000)
    raw_res = residual(sol.x) * sigma_mm
    per_anchor: dict[str, list[float]] = defaultdict(list)
    for r, err in zip(obs, raw_res):
        per_anchor[ANCHORS[int(r["anchor"])]].append(float(err))
    return {
        "position_mm": [float(v) for v in sol.x[:3]],
        "d_tag_mm": float(sol.x[3]),
        "rms_mm": float(math.sqrt(float(np.mean(raw_res * raw_res)))) if raw_res.size else 0.0,
        "median_abs_residual_mm": float(median([abs(float(v)) for v in raw_res])) if raw_res.size else 0.0,
        "max_abs_residual_mm": float(max(abs(float(v)) for v in raw_res)) if raw_res.size else 0.0,
        "n_observations": len(obs),
        "n_anchors_used": len({int(r["anchor"]) for r in obs}),
        "per_anchor_rms_mm": {
            k: float(math.sqrt(float(np.mean(np.asarray(v) ** 2)))) for k, v in sorted(per_anchor.items())
        },
        "success": bool(sol.success),
        "message": sol.message,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Calibrate per-tag delay from stationary tag-to-anchor ranges.")
    ap.add_argument("--anchor-layout", required=True, help="Anchor layout JSON with anchors[] or APOS layout{}")
    ap.add_argument("--anchor-delays", help="Optional JSON containing delay_mm per anchor")
    ap.add_argument("--range-csv", "--cr-csv", dest="range_csv", required=True, help="tr_all.csv, cr_all.csv, recv dir, or capture root")
    ap.add_argument("--output", required=True)
    ap.add_argument("--sigma-mm", type=float, default=50.0)
    ap.add_argument("--min-quality", type=float, default=50.0)
    args = ap.parse_args()

    layout_path = Path(args.anchor_layout)
    delay_path = Path(args.anchor_delays) if args.anchor_delays else None
    range_path = Path(args.range_csv)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    anchor_xyz = load_anchor_layout(layout_path)
    anchor_delay = load_anchor_delays(delay_path)
    ranges_by_tag = read_ranges(range_path, args.min_quality)

    tags = []
    for idx, (tag, rows) in enumerate(sorted(ranges_by_tag.items())):
        med_obs = median_by_anchor(rows)
        if len(med_obs) < 4:
            continue
        median_solution = solve_tag_delay(anchor_xyz, anchor_delay, med_obs, args.sigma_mm)
        all_solution = solve_tag_delay(
            anchor_xyz,
            anchor_delay,
            rows,
            args.sigma_mm,
            x0_pos=np.asarray(median_solution["position_mm"], dtype=float),
        )
        tags.append(
            {
                "tag_id": idx,
                "name": tag,
                "median_solution": median_solution,
                "all_observations_solution": all_solution,
                "d_tag_mm": all_solution["d_tag_mm"],
                "position_mm": all_solution["position_mm"],
                "rms_mm": all_solution["rms_mm"],
                "n_observations": all_solution["n_observations"],
                "n_anchors_used": all_solution["n_anchors_used"],
                "median_ranges_by_anchor": [
                    {
                        "anchor": ANCHORS[int(r["anchor"])],
                        "anchor_id": int(r["anchor"]),
                        "range_mm": r["range_mm"],
                        "n": r["n"],
                        "quality": r["quality"],
                    }
                    for r in med_obs
                ],
            }
        )

    payload = {
        "schema": "tag_delay_calibration_v1",
        "units": "mm",
        "tags": tags,
        "reference": {
            "anchor_layout": str(layout_path),
            "anchor_delays": str(delay_path) if delay_path else "zeros",
            "range_csv": str(find_range_csv(range_path)),
            "sigma_mm": args.sigma_mm,
            "min_quality": args.min_quality,
        },
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"[ok] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
