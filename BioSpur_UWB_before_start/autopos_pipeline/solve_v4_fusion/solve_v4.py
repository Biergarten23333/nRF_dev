#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix


ANCHORS = "ABCDEFGH"


def load_layout(path: Path) -> np.ndarray:
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


def tag_names_from_ranges(tag_ranges: list[dict[str, Any]]) -> list[str]:
    names = sorted({str(r.get("tag", "")).strip() for r in tag_ranges if str(r.get("tag", "")).strip()})
    return names


def build_tag_frame_map(
    tag_ranges: list[dict[str, Any]],
    tag_to_id: dict[str, int],
    subsample: int,
) -> tuple[list[tuple[int, str, str]], dict[tuple[int, str, str], int], list[dict[str, Any]]]:
    grouped: dict[tuple[int, str, str], int] = defaultdict(int)
    for r in tag_ranges:
        tag = str(r.get("tag", "")).strip()
        if tag not in tag_to_id:
            continue
        # Sweep counters restart across capture folders. Include source so
        # physically different placements from ID01-ID31 never collapse into
        # the same nuisance tag-position variable.
        key = (tag_to_id[tag], str(r.get("source") or ""), str(r.get("sweep") or r.get("t")))
        grouped[key] += 1
    keys = [k for k, n in grouped.items() if n >= 4]
    keys.sort()
    if subsample > 1:
        keys = keys[::subsample]
    frame_map = {k: i for i, k in enumerate(keys)}
    filtered = []
    for r in tag_ranges:
        tag = str(r.get("tag", "")).strip()
        if tag not in tag_to_id:
            continue
        key = (tag_to_id[tag], str(r.get("source") or ""), str(r.get("sweep") or r.get("t")))
        if key in frame_map:
            rr = dict(r)
            rr["tag_id"] = tag_to_id[tag]
            rr["frame_key"] = key
            filtered.append(rr)
    return keys, frame_map, filtered


def initial_tag_positions(
    frame_keys: list[tuple[int, str, str]],
    frame_map: dict[tuple[int, str, str], int],
    tag_ranges: list[dict[str, Any]],
    tag_pos_rows: list[dict[str, Any]],
    tag_to_id: dict[str, int],
    anchor_xyz: np.ndarray,
) -> np.ndarray:
    by_key: dict[tuple[int, str, str], np.ndarray] = {}
    for r in tag_pos_rows:
        tag = str(r.get("tag", "")).strip()
        if tag not in tag_to_id:
            continue
        key = (tag_to_id[tag], str(r.get("source") or ""), str(r.get("sweep") or r.get("t")))
        try:
            by_key[key] = np.array([float(r["x_mm"]), float(r["y_mm"]), float(r["z_mm"])], dtype=float)
        except Exception:
            pass

    ranges_by_key: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for r in tag_ranges:
        ranges_by_key[r["frame_key"]].append(r)

    centroid = np.mean(anchor_xyz, axis=0)
    centroid[2] = max(300.0, min(1500.0, centroid[2]))
    out = np.zeros((len(frame_keys), 3), dtype=float)
    for key in frame_keys:
        idx = frame_map[key]
        if key in by_key:
            out[idx] = by_key[key]
            continue
        rows = ranges_by_key.get(key) or []
        if not rows:
            out[idx] = centroid
            continue
        weights = []
        points = []
        for r in rows:
            aid = int(r["anchor"])
            rng = max(float(r["range_mm"]), 100.0)
            weights.append(1.0 / rng)
            points.append(anchor_xyz[aid])
        out[idx] = np.average(np.asarray(points, dtype=float), axis=0, weights=np.asarray(weights, dtype=float))
    return out


def pack_variables(anchor_xyz: np.ndarray, d_anchor: np.ndarray, d_tag: np.ndarray, tag_positions: np.ndarray) -> np.ndarray:
    vals = np.zeros(33 + len(tag_positions) * 3, dtype=float)
    vals[0:24] = anchor_xyz.reshape(-1)
    vals[24:31] = d_anchor[1:8]
    vals[31:33] = d_tag[1:3]
    vals[33:] = tag_positions.reshape(-1)
    return vals


def variable_bounds(n_tag_frames: int, delay_bound_mm: float) -> tuple[np.ndarray, np.ndarray]:
    n = 33 + n_tag_frames * 3
    lower = np.full(n, -np.inf, dtype=float)
    upper = np.full(n, np.inf, dtype=float)
    lower[24:33] = -abs(delay_bound_mm)
    upper[24:33] = abs(delay_bound_mm)
    return lower, upper


def unpack_variables(x: np.ndarray, n_tag_frames: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    anchor_xyz = x[0:24].reshape((8, 3)).copy()
    d_anchor = np.zeros(8, dtype=float)
    d_anchor[1:8] = x[24:31]
    d_tag = np.zeros(3, dtype=float)
    d_tag[1:3] = x[31:33]
    tag_positions = x[33:].reshape((n_tag_frames, 3)).copy()
    return anchor_xyz, d_anchor, d_tag, tag_positions


def residuals(
    x: np.ndarray,
    inter_ranges: list[dict[str, Any]],
    tag_ranges: list[dict[str, Any]],
    frame_map: dict[tuple[int, str], int],
    config: SimpleNamespace,
) -> np.ndarray:
    anchor_xyz, d_anchor, d_tag, tag_pos = unpack_variables(x, config.n_tag_frames)
    res: list[float] = []

    for obs in inter_ranges:
        i = int(obs["i"])
        j = int(obs["j"])
        pred = float(np.linalg.norm(anchor_xyz[i] - anchor_xyz[j]) + d_anchor[i] + d_anchor[j])
        res.append((pred - float(obs["range_mm"])) / config.sigma_inter)

    for obs in tag_ranges:
        aid = int(obs["anchor"])
        tid = int(obs["tag_id"])
        frame_idx = frame_map[obs["frame_key"]]
        pred = float(np.linalg.norm(tag_pos[frame_idx] - anchor_xyz[aid]) + d_anchor[aid] + d_tag[tid])
        res.append((pred - float(obs["range_mm"])) / config.sigma_tag)

    # Gauge only: define coordinate frame without imposing a rectangular box.
    res.extend((anchor_xyz[0] / config.sigma_gauge).tolist())  # A at origin.
    res.append(float(anchor_xyz[1, 1]) / config.sigma_gauge)   # B on x-axis.
    res.append(float(anchor_xyz[1, 2]) / config.sigma_gauge)
    res.append(float(anchor_xyz[2, 2]) / config.sigma_gauge)   # Fix remaining rotation about x-axis.

    # Soft delay priors. d_anchor[0] and d_tag[0] are fixed by packing.
    res.extend((d_anchor[1:] / config.sigma_delay_prior).tolist())
    res.extend((d_tag[1:] / config.sigma_delay_prior).tolist())
    return np.asarray(res, dtype=float)


def build_jac_sparsity(
    inter_ranges: list[dict[str, Any]],
    tag_ranges: list[dict[str, Any]],
    frame_map: dict[tuple[int, str, str], int],
    n_tag_frames: int,
) -> lil_matrix:
    """Sparse finite-difference structure for the joint V4 residual.

    Without this, scipy estimates a dense Jacobian over every tag-position
    nuisance variable, which is prohibitively slow once many captures are fused.
    Each range residual only touches two anchors or one anchor plus one tag
    frame, so the Jacobian is extremely sparse.
    """
    n_vars = 33 + n_tag_frames * 3
    n_res = len(inter_ranges) + len(tag_ranges) + 3 + 2 + 1 + 7 + 2
    mat = lil_matrix((n_res, n_vars), dtype=int)

    row = 0
    for obs in inter_ranges:
        i = int(obs["i"])
        j = int(obs["j"])
        mat[row, 3 * i : 3 * i + 3] = 1
        mat[row, 3 * j : 3 * j + 3] = 1
        if i > 0:
            mat[row, 24 + i - 1] = 1
        if j > 0:
            mat[row, 24 + j - 1] = 1
        row += 1

    for obs in tag_ranges:
        aid = int(obs["anchor"])
        tid = int(obs["tag_id"])
        frame_idx = frame_map[obs["frame_key"]]
        mat[row, 3 * aid : 3 * aid + 3] = 1
        if aid > 0:
            mat[row, 24 + aid - 1] = 1
        if tid > 0:
            mat[row, 31 + tid - 1] = 1
        t0 = 33 + 3 * frame_idx
        mat[row, t0 : t0 + 3] = 1
        row += 1

    # Gauge: A xyz, B y/z, C z.
    mat[row, 0] = 1
    row += 1
    mat[row, 1] = 1
    row += 1
    mat[row, 2] = 1
    row += 1
    mat[row, 4] = 1
    row += 1
    mat[row, 5] = 1
    row += 1
    mat[row, 8] = 1
    row += 1

    # Delay priors.
    for i in range(1, 8):
        mat[row, 24 + i - 1] = 1
        row += 1
    for tid in range(1, 3):
        mat[row, 31 + tid - 1] = 1
        row += 1

    return mat


def split_residuals(
    anchor_xyz: np.ndarray,
    d_anchor: np.ndarray,
    d_tag: np.ndarray,
    tag_positions: np.ndarray,
    inter_ranges: list[dict[str, Any]],
    tag_ranges: list[dict[str, Any]],
    frame_map: dict[tuple[int, str], int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inter = []
    for obs in inter_ranges:
        i = int(obs["i"])
        j = int(obs["j"])
        pred = float(np.linalg.norm(anchor_xyz[i] - anchor_xyz[j]) + d_anchor[i] + d_anchor[j])
        err = pred - float(obs["range_mm"])
        inter.append({"pair": f"{ANCHORS[i]}-{ANCHORS[j]}", "err_mm": err, "abs_err_mm": abs(err)})
    tag = []
    for obs in tag_ranges:
        aid = int(obs["anchor"])
        tid = int(obs["tag_id"])
        frame_idx = frame_map[obs["frame_key"]]
        pred = float(np.linalg.norm(tag_positions[frame_idx] - anchor_xyz[aid]) + d_anchor[aid] + d_tag[tid])
        err = pred - float(obs["range_mm"])
        tag.append(
            {
                "tag_id": tid,
                "tag_name": str(obs.get("tag", "")),
                "anchor_id": aid,
                "anchor": ANCHORS[aid],
                "err_mm": err,
                "abs_err_mm": abs(err),
            }
        )
    inter.sort(key=lambda r: r["abs_err_mm"], reverse=True)
    tag.sort(key=lambda r: r["abs_err_mm"], reverse=True)
    return inter, tag


def rms(vals: list[float]) -> float:
    arr = np.asarray(vals, dtype=float)
    return float(np.sqrt(np.mean(arr * arr))) if arr.size else 0.0


def per_anchor_tag_rms(tag_errors: list[dict[str, Any]]) -> dict[str, float]:
    groups: dict[str, list[float]] = defaultdict(list)
    for r in tag_errors:
        groups[str(r["anchor"])].append(float(r["err_mm"]))
    return {k: rms(v) for k, v in sorted(groups.items())}


def per_tag_rms(tag_errors: list[dict[str, Any]]) -> dict[str, float]:
    groups: dict[str, list[float]] = defaultdict(list)
    for r in tag_errors:
        groups[str(r["tag_name"])].append(float(r["err_mm"]))
    return {k: rms(v) for k, v in sorted(groups.items())}


def main() -> int:
    ap = argparse.ArgumentParser(description="AutoPos V4 joint anchor/tag delay fusion solver.")
    ap.add_argument("--data", required=True, help="V4 data JSON from prepare_v4_data.py")
    ap.add_argument("--init-layout", required=True, help="Initial anchor layout JSON")
    ap.add_argument("--output", required=True, help="Output layout JSON")
    ap.add_argument("--tag-subsample", type=int, default=10)
    ap.add_argument("--sigma-inter-mm", "--sigma-inter", dest="sigma_inter_mm", type=float, default=30.0)
    ap.add_argument("--sigma-tag-mm", "--sigma-tag", dest="sigma_tag_mm", type=float, default=80.0)
    ap.add_argument("--sigma-gauge-mm", type=float, default=1.0)
    ap.add_argument("--sigma-delay-prior-mm", type=float, default=10.0)
    ap.add_argument("--delay-bound-mm", type=float, default=30.0)
    ap.add_argument("--loss", default="huber", choices=["linear", "soft_l1", "huber", "cauchy", "arctan"])
    ap.add_argument(
        "--f-scale",
        type=float,
        default=30.0,
        help="Robust loss transition in mm, converted to normalized units using sigma_inter_mm",
    )
    ap.add_argument("--max-nfev", type=int, default=500)
    ap.add_argument("--verbose", type=int, default=1)
    args = ap.parse_args()

    data_path = Path(args.data)
    init_path = Path(args.init_layout)
    data = json.loads(data_path.read_text(encoding="utf-8"))
    inter_ranges = data.get("inter_anchor_ranges") or []
    tag_ranges_all = data.get("tag_anchor_ranges") or []
    if not inter_ranges:
        raise SystemExit("[error] no inter_anchor_ranges in data")
    if not tag_ranges_all:
        raise SystemExit("[error] no tag_anchor_ranges in data")

    tag_names = tag_names_from_ranges(tag_ranges_all)
    if len(tag_names) > 3:
        raise SystemExit(f"[error] current joint model supports up to 3 tags, got {tag_names}")
    tag_to_id = {name: idx for idx, name in enumerate(tag_names)}

    frame_keys, frame_map, tag_ranges = build_tag_frame_map(tag_ranges_all, tag_to_id, args.tag_subsample)
    if not tag_ranges:
        raise SystemExit("[error] no tag ranges survived frame subsampling")

    anchor_init = load_layout(init_path)
    tag_pos_init = initial_tag_positions(
        frame_keys,
        frame_map,
        tag_ranges,
        data.get("tag_position_initializers") or [],
        tag_to_id,
        anchor_init,
    )
    d_anchor_init = np.zeros(8, dtype=float)
    d_tag_init = np.zeros(3, dtype=float)
    x0 = pack_variables(anchor_init, d_anchor_init, d_tag_init, tag_pos_init)

    config = SimpleNamespace(
        sigma_inter=args.sigma_inter_mm,
        sigma_tag=args.sigma_tag_mm,
        sigma_gauge=args.sigma_gauge_mm,
        sigma_delay_prior=args.sigma_delay_prior_mm,
        n_tag_frames=len(frame_keys),
    )

    fun = lambda x: residuals(x, inter_ranges, tag_ranges, frame_map, config)
    bounds = variable_bounds(len(frame_keys), args.delay_bound_mm)
    # Residuals are normalized by their sigma. Interpret --f-scale as a
    # physical millimeter threshold for inter-anchor residuals, then convert to
    # scipy's normalized residual units. This keeps CLI usage readable:
    # --sigma-inter 15 --f-scale 30 means Huber starts around 30mm for inter.
    normalized_f_scale = max(args.f_scale / max(args.sigma_inter_mm, 1e-9), 1e-9)
    result = least_squares(
        fun,
        x0,
        loss=args.loss,
        f_scale=normalized_f_scale,
        bounds=bounds,
        jac_sparsity=build_jac_sparsity(inter_ranges, tag_ranges, frame_map, len(frame_keys)),
        max_nfev=args.max_nfev,
        verbose=args.verbose,
    )
    anchor_xyz, d_anchor, d_tag, tag_positions = unpack_variables(result.x, len(frame_keys))
    inter_errors, tag_errors = split_residuals(
        anchor_xyz,
        d_anchor,
        d_tag,
        tag_positions,
        inter_ranges,
        tag_ranges,
        frame_map,
    )

    inter_rms = rms([float(r["err_mm"]) for r in inter_errors])
    tag_rms = rms([float(r["err_mm"]) for r in tag_errors])
    inter_abs = [float(r["abs_err_mm"]) for r in inter_errors]
    tag_abs = [float(r["abs_err_mm"]) for r in tag_errors]

    def threshold_stats(values: list[float], threshold: float) -> dict[str, float | int]:
        kept = [v for v in values if v <= threshold]
        return {
            "threshold_mm": float(threshold),
            "n": int(len(kept)),
            "rms_mm": rms(kept),
            "median_abs_mm": float(np.median(np.asarray(kept, dtype=float))) if kept else 0.0,
        }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "autopos_v4_joint_layout_v1",
        "units": "mm",
        "anchors": [
            {
                "id": i,
                "label": ANCHORS[i],
                "x_mm": float(anchor_xyz[i, 0]),
                "y_mm": float(anchor_xyz[i, 1]),
                "z_mm": float(anchor_xyz[i, 2]),
                "d_anchor_mm": float(d_anchor[i]),
                "delay_mm": float(d_anchor[i]),
            }
            for i in range(8)
        ],
        "tags": [
            {
                "id": idx,
                "name": name,
                "d_tag_mm": float(d_tag[idx]),
            }
            for name, idx in sorted(tag_to_id.items(), key=lambda kv: kv[1])
        ],
        "stats": {
            "inter_anchor_rms_mm": inter_rms,
            "tag_anchor_rms_mm": tag_rms,
            "per_anchor_tag_rms_mm": per_anchor_tag_rms(tag_errors),
            "per_tag_rms_mm": per_tag_rms(tag_errors),
            "top_inter_errors": inter_errors[:10],
            "top_tag_errors": tag_errors[:20],
            "inter_inlier_stats": {
                "abs_le_30mm": threshold_stats(inter_abs, 30.0),
                "abs_le_50mm": threshold_stats(inter_abs, 50.0),
                "abs_le_100mm": threshold_stats(inter_abs, 100.0),
            },
            "tag_inlier_stats": {
                "abs_le_50mm": threshold_stats(tag_abs, 50.0),
                "abs_le_100mm": threshold_stats(tag_abs, 100.0),
                "abs_le_150mm": threshold_stats(tag_abs, 150.0),
            },
            "total_variables": int(len(result.x)),
            "total_residuals": int(fun(result.x).size),
            "n_inter_obs": int(len(inter_ranges)),
            "n_tag_obs": int(len(tag_ranges)),
            "n_tag_frames": int(len(frame_keys)),
            "optimizer_cost": float(result.cost),
            "optimizer_nfev": int(result.nfev),
            "optimizer_status": int(result.status),
            "optimizer_success": bool(result.success),
            "optimizer_message": result.message,
        },
        "config": {
            "sigma_inter_mm": args.sigma_inter_mm,
            "sigma_tag_mm": args.sigma_tag_mm,
            "sigma_gauge_mm": args.sigma_gauge_mm,
            "sigma_delay_prior_mm": args.sigma_delay_prior_mm,
            "delay_bound_mm": args.delay_bound_mm,
            "loss": args.loss,
            "f_scale_mm": args.f_scale,
            "normalized_f_scale": normalized_f_scale,
            "tag_subsample": args.tag_subsample,
            "tag_names": tag_names,
            "gauge": "A at origin, B on x-axis, C.z=0 to fix rotation about x-axis",
        },
        "source": {
            "data": str(data_path),
            "init_layout": str(init_path),
            "old_solver_backup": "autopos_pipeline/solve_v4_fusion/solve_v4_old.py",
        },
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["stats"], indent=2))
    print("delays_anchor", {ANCHORS[i]: round(float(d_anchor[i]), 3) for i in range(8)})
    print("delays_tag", {name: round(float(d_tag[idx]), 3) for name, idx in sorted(tag_to_id.items(), key=lambda kv: kv[1])})
    print(f"[ok] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
