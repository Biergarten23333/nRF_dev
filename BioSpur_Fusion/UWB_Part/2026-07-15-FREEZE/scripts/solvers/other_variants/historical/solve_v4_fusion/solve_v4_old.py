#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    if "anchors" in raw:
        for ent in raw["anchors"]:
            if "label" in ent:
                idx = ANCHORS.index(str(ent["label"]).upper())
            else:
                idx = int(ent["id"])
            coords[idx] = [float(ent["x_mm"]), float(ent["y_mm"]), float(ent["z_mm"])]
    elif "layout" in raw:
        for key, val in raw["layout"].items():
            idx = int(key)
            coords[idx] = [float(val[0]), float(val[1]), float(val[2])]
    else:
        raise KeyError(f"{path} has neither 'anchors' nor APOS 'layout'")
    return coords


def pack(anchors: np.ndarray, tag_positions: np.ndarray, delays: np.ndarray | None) -> np.ndarray:
    vals: list[float] = []
    # Gauge-fixed anchors:
    # A=(0,0,0), B=(Bx,0,0), D=(Dx,Dy,0), C/E/F/G/H free as needed.
    vals.append(float(anchors[1, 0]))
    vals.extend(anchors[2].tolist())
    vals.extend([float(anchors[3, 0]), float(anchors[3, 1])])
    for idx in (4, 5, 6, 7):
        vals.extend(anchors[idx].tolist())
    vals.extend(tag_positions.reshape(-1).tolist())
    if delays is not None:
        # D0 is fixed to zero; optimize D1..D7.
        vals.extend(delays[1:].tolist())
    return np.asarray(vals, dtype=float)


def unpack(x: np.ndarray, n_tags: int, with_delays: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    idx = 0
    anchors = np.zeros((8, 3), dtype=float)
    anchors[1] = [x[idx], 0.0, 0.0]
    idx += 1
    anchors[2] = x[idx : idx + 3]
    idx += 3
    anchors[3] = [x[idx], x[idx + 1], 0.0]
    idx += 2
    for aid in (4, 5, 6, 7):
        anchors[aid] = x[idx : idx + 3]
        idx += 3
    tag_positions = x[idx : idx + 3 * n_tags].reshape((n_tags, 3)).copy()
    idx += 3 * n_tags
    delays = None
    if with_delays:
        delays = np.zeros((8,), dtype=float)
        delays[1:] = x[idx : idx + 7]
    return anchors, tag_positions, delays


def make_tag_keys(tag_ranges: list[dict[str, Any]], subsample: int) -> tuple[list[tuple[str, str]], dict[tuple[str, str], int]]:
    grouped: dict[tuple[str, str], int] = defaultdict(int)
    for r in tag_ranges:
        grouped[(str(r.get("tag", "")), str(r.get("sweep") or r.get("t")))] += 1
    keys = [k for k, n in grouped.items() if n >= 4]
    keys.sort()
    if subsample > 1:
        keys = keys[::subsample]
    return keys, {k: i for i, k in enumerate(keys)}


def initial_tag_positions(
    keys: list[tuple[str, str]],
    tag_pos_rows: list[dict[str, Any]],
    anchor_init: np.ndarray,
) -> np.ndarray:
    by_key: dict[tuple[str, str], np.ndarray] = {}
    for r in tag_pos_rows:
        key = (str(r.get("tag", "")), str(r.get("sweep") or r.get("t")))
        try:
            by_key[key] = np.array([float(r["x_mm"]), float(r["y_mm"]), float(r["z_mm"])], dtype=float)
        except Exception:
            pass
    centroid = np.mean(anchor_init, axis=0)
    centroid[2] = max(500.0, min(1400.0, centroid[2]))
    out = np.zeros((len(keys), 3), dtype=float)
    for i, key in enumerate(keys):
        out[i] = by_key.get(key, centroid)
    return out


def residuals(
    x: np.ndarray,
    *,
    n_tags: int,
    with_delays: bool,
    inter: list[dict[str, Any]],
    tag_ranges: list[dict[str, Any]],
    tag_key_to_idx: dict[tuple[str, str], int],
    lower_sigma: float,
    upper_sigma: float,
    band_prior: float,
    band_sigma: float,
    delay_sigma: float,
) -> np.ndarray:
    anchors, tags, delays = unpack(x, n_tags, with_delays)
    res: list[float] = []
    d = delays if delays is not None else np.zeros((8,), dtype=float)

    for obs in inter:
        i = int(obs["i"])
        j = int(obs["j"])
        pred = float(np.linalg.norm(anchors[i] - anchors[j]) + d[i] + d[j])
        sig = max(1.0, float(obs.get("sigma_mm") or 50.0))
        res.append((pred - float(obs["range_mm"])) / sig)

    for obs in tag_ranges:
        key = (str(obs.get("tag", "")), str(obs.get("sweep") or obs.get("t")))
        ti = tag_key_to_idx.get(key)
        if ti is None:
            continue
        aid = int(obs["anchor"])
        pred = float(np.linalg.norm(tags[ti] - anchors[aid]) + d[aid])
        sig = max(1.0, float(obs.get("sigma_mm") or 80.0))
        res.append((pred - float(obs["range_mm"])) / sig)

    # Weak high-DOF geometry priors: height bands only, no rectangle/column prior.
    if lower_sigma > 0:
        res.append(float(anchors[2, 2]) / lower_sigma)
    if upper_sigma > 0:
        uz = anchors[4:8, 2]
        res.extend(((uz - np.mean(uz)) / upper_sigma).tolist())
    if band_sigma > 0:
        lower_mean = float(np.mean(anchors[:4, 2]))
        upper_mean = float(np.mean(anchors[4:8, 2]))
        res.append(((upper_mean - lower_mean) - band_prior) / band_sigma)
    if with_delays and delay_sigma > 0 and delays is not None:
        res.extend((delays[1:] / delay_sigma).tolist())
    return np.asarray(res, dtype=float)


def edge_stats(anchors: np.ndarray, delays: np.ndarray, inter: list[dict[str, Any]]) -> dict[str, Any]:
    errs = []
    per = []
    for obs in inter:
        i = int(obs["i"])
        j = int(obs["j"])
        pred = float(np.linalg.norm(anchors[i] - anchors[j]) + delays[i] + delays[j])
        err = pred - float(obs["range_mm"])
        errs.append(err)
        per.append({"pair": f"{ANCHORS[i]}-{ANCHORS[j]}", "err_mm": err, "abs_err_mm": abs(err)})
    arr = np.asarray(errs, dtype=float)
    per.sort(key=lambda r: r["abs_err_mm"], reverse=True)
    return {
        "rms_mm": float(np.sqrt(np.mean(arr * arr))) if arr.size else 0.0,
        "top_errors": per[:10],
    }


def tag_stats(anchors: np.ndarray, tags: np.ndarray, delays: np.ndarray, tag_ranges: list[dict[str, Any]], key_map: dict[tuple[str, str], int]) -> dict[str, Any]:
    errs = []
    by_anchor: dict[int, list[float]] = defaultdict(list)
    for obs in tag_ranges:
        key = (str(obs.get("tag", "")), str(obs.get("sweep") or obs.get("t")))
        ti = key_map.get(key)
        if ti is None:
            continue
        aid = int(obs["anchor"])
        pred = float(np.linalg.norm(tags[ti] - anchors[aid]) + delays[aid])
        err = pred - float(obs["range_mm"])
        errs.append(err)
        by_anchor[aid].append(err)
    arr = np.asarray(errs, dtype=float)
    return {
        "rms_mm": float(np.sqrt(np.mean(arr * arr))) if arr.size else 0.0,
        "count": int(arr.size),
        "per_anchor_rms_mm": {
            ANCHORS[i]: float(np.sqrt(np.mean(np.asarray(v) ** 2))) for i, v in sorted(by_anchor.items())
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="AutoPos V4 Phase A/B SciPy fusion solver.")
    ap.add_argument("--data", required=True, help="V4 data JSON from prepare_v4_data.py")
    ap.add_argument("--init-layout", required=True, help="Initial anchor layout JSON")
    ap.add_argument("--output", required=True, help="Output layout JSON")
    ap.add_argument("--phase", choices=("A", "B"), default="A", help="A=no delay variables, B=anchor delay variables")
    ap.add_argument("--tag-subsample", type=int, default=10)
    ap.add_argument("--lower-plane-sigma-mm", type=float, default=140.0)
    ap.add_argument("--upper-level-sigma-mm", type=float, default=120.0)
    ap.add_argument("--band-separation-prior-mm", type=float, default=1600.0)
    ap.add_argument("--band-separation-sigma-mm", type=float, default=350.0)
    ap.add_argument("--delay-sigma-mm", type=float, default=5.0)
    ap.add_argument("--max-nfev", type=int, default=500)
    args = ap.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    inter = data.get("inter_anchor_ranges") or []
    tag_ranges_all = data.get("tag_anchor_ranges") or []
    if not inter:
        raise SystemExit("[error] no inter_anchor_ranges in data")

    keys, key_map = make_tag_keys(tag_ranges_all, args.tag_subsample)
    tag_ranges = [r for r in tag_ranges_all if (str(r.get("tag", "")), str(r.get("sweep") or r.get("t"))) in key_map]
    if not tag_ranges:
        print("[warn] no tag-anchor ranges available; solving inter-anchor-only high-DOF layout")

    anchors0 = load_layout(Path(args.init_layout))
    tags0 = initial_tag_positions(keys, data.get("tag_position_initializers") or [], anchors0)
    delays0 = np.zeros((8,), dtype=float) if args.phase == "B" else None
    x0 = pack(anchors0, tags0, delays0)

    fun = lambda x: residuals(
        x,
        n_tags=len(keys),
        with_delays=args.phase == "B",
        inter=inter,
        tag_ranges=tag_ranges,
        tag_key_to_idx=key_map,
        lower_sigma=args.lower_plane_sigma_mm,
        upper_sigma=args.upper_level_sigma_mm,
        band_prior=args.band_separation_prior_mm,
        band_sigma=args.band_separation_sigma_mm,
        delay_sigma=args.delay_sigma_mm,
    )
    loss = "soft_l1" if tag_ranges else "linear"
    sol = least_squares(fun, x0, loss=loss, f_scale=2.0, max_nfev=args.max_nfev, verbose=1)
    anchors, tags, delays = unpack(sol.x, len(keys), args.phase == "B")
    if delays is None:
        delays = np.zeros((8,), dtype=float)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "autopos_v4_layout_v1",
        "units": "mm",
        "phase": args.phase,
        "anchors": [
            {
                "id": i,
                "label": ANCHORS[i],
                "x_mm": float(anchors[i, 0]),
                "y_mm": float(anchors[i, 1]),
                "z_mm": float(anchors[i, 2]),
                "delay_mm": float(delays[i]),
            }
            for i in range(8)
        ],
        "stats": {
            "inter_anchor": edge_stats(anchors, delays, inter),
            "tag_anchor": tag_stats(anchors, tags, delays, tag_ranges, key_map),
            "inter_factors": len(inter),
            "tag_factors": len(tag_ranges),
            "tag_position_variables": len(keys),
            "total_residuals": int(fun(sol.x).size),
            "cost": float(sol.cost),
            "nfev": int(sol.nfev),
            "success": bool(sol.success),
            "message": sol.message,
            "data_warning": data.get("stats", {}).get("warning", ""),
        },
        "source": {
            "data": str(Path(args.data)),
            "init_layout": str(Path(args.init_layout)),
            "tag_subsample": args.tag_subsample,
            "note": "SciPy implementation because python-gtsam is not installed in this environment.",
        },
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["stats"], indent=2))
    print(f"[ok] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
