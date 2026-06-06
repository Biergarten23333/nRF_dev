#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import itertools
import json
import math
import os
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


REPO = Path(__file__).resolve().parents[1]
EVAL_PATH = REPO / "autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py"
REFERENCE_SWEEP = REPO / "autopos_pipeline/28052026_Erlangen_Official/solver/work/field_dataset_staged/sweep1000/pairs_all.csv"
ANCHORS = "ABCDEFGH"
SOLVER_NAMES = ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
SOLVER_TO_MAIN = {
    "v1-old": "AutoPos V1",
    "v2": "AutoPos V2",
    "v3-lite": "V3-lite",
    "v3-full": "V3-full",
    "v4-io": "V4-interonly",
}


def load_eval_module():
    spec = importlib.util.spec_from_file_location("autopos_mainline_eval", EVAL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {EVAL_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def anchor_idx(v: str) -> int:
    s = str(v).strip().upper()
    if s in ANCHORS:
        return ANCHORS.index(s)
    return int(s)


def rms(vals: list[float] | np.ndarray) -> float:
    arr = np.asarray(vals, dtype=float)
    return float(np.sqrt(np.mean(arr * arr))) if arr.size else float("nan")


def robust_sigma(vals: list[float], floor: float = 3.0) -> float:
    arr = np.asarray(vals, dtype=float)
    if arr.size < 2:
        return floor
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    return max(floor, 1.4826 * mad)


def load_reference_noise(path: Path) -> dict[tuple[int, int], np.ndarray]:
    directed: dict[tuple[int, int], list[float]] = defaultdict(list)
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                a = anchor_idx(row["a"])
                b = anchor_idx(row["b"])
                master = anchor_idx(row.get("master") or row["a"])
                d = float(row.get("dist_mm") or row.get("raw_mm"))
                ok = int(float(row.get("ok") or 1))
                q = float(row.get("quality_percent") or 100)
            except Exception:
                continue
            if a == b or d <= 0 or not ok or q <= 0:
                continue
            if master == a:
                directed[(a, b)].append(d)
            elif master == b:
                directed[(b, a)].append(d)
            else:
                directed[(a, b)].append(d)

    residuals: dict[tuple[int, int], np.ndarray] = {}
    for key, vals in directed.items():
        arr = np.asarray(vals, dtype=float)
        if arr.size:
            residuals[key] = arr - float(np.median(arr))
    return residuals


def gauge_align_local(x: np.ndarray) -> np.ndarray:
    out = np.asarray(x, dtype=float).copy()
    out -= out[0]
    bx = out[1]
    bn = np.linalg.norm(bx)
    if bn < 1e-9:
        return out
    ex = bx / bn
    c = out[2]
    c_perp = c - np.dot(c, ex) * ex
    ey = np.array([0.0, 1.0, 0.0]) if np.linalg.norm(c_perp) < 1e-9 else c_perp / np.linalg.norm(c_perp)
    ez = np.cross(ex, ey)
    rot = np.vstack([ex, ey, ez]).T
    out = out @ rot
    out[0] = 0.0
    out[1, 1:] = 0.0
    out[2, 2] = 0.0
    return out


def normalize_extent_mm(x: np.ndarray, max_extent_mm: float = 5000.0) -> np.ndarray:
    out = x.copy()
    mins = np.min(out, axis=0)
    maxs = np.max(out, axis=0)
    span = maxs - mins
    scale = min(1.0, max_extent_mm / max(float(np.max(span)), 1.0))
    return out * scale


def polygon_area_xy(points: np.ndarray) -> float:
    x = points[:, 0]
    y = points[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def is_concave_xy(points: np.ndarray) -> bool:
    signs = []
    for i in range(len(points)):
        a = points[i]
        b = points[(i + 1) % len(points)]
        c = points[(i + 2) % len(points)]
        cross = np.cross(np.r_[b - a, 0.0], np.r_[c - b, 0.0])[2]
        if abs(cross) > 1e-6:
            signs.append(math.copysign(1.0, cross))
    return bool(signs and min(signs) < 0 < max(signs))


def passes_extent(xyz: np.ndarray, min_xy_span_mm: float, min_z_span_mm: float) -> bool:
    span = np.max(xyz, axis=0) - np.min(xyz, axis=0)
    if min_xy_span_mm > 0 and (span[0] < min_xy_span_mm or span[1] < min_xy_span_mm):
        return False
    if min_z_span_mm > 0 and span[2] < min_z_span_mm:
        return False
    return True


def generate_control_5x5_layout(rng: np.random.Generator, layout_id: int) -> tuple[np.ndarray, dict[str, Any]]:
    lower = np.array(
        [
            [0.0, 0.0, 0.0],
            [5000.0, 0.0, 0.0],
            [5000.0, 5000.0, 0.0],
            [0.0, 5000.0, 0.0],
        ],
        dtype=float,
    )
    # Tiny deterministic-looking jitter avoids an unrealistically perfect graph
    # while preserving this as the canonical 5 m footprint control.
    lower[:, :2] += rng.normal(0.0, 15.0, size=(4, 2))
    lower[:, 2] += rng.normal(0.0, 10.0, size=4)
    upper = lower.copy()
    upper[:, 2] -= rng.normal(1400.0, 20.0, size=4)
    upper[:, :2] += rng.normal(0.0, 20.0, size=(4, 2))
    xyz = gauge_align_local(normalize_extent_mm(np.vstack([lower, upper])))
    span = np.max(xyz, axis=0) - np.min(xyz, axis=0)
    side_lengths = [np.linalg.norm(xyz[i, :2] - xyz[(i + 1) % 4, :2]) for i in range(4)]
    pair_gaps = [abs(xyz[i + 4, 2] - xyz[i, 2]) for i in range(4)]
    meta = {
        "layout_id": layout_id,
        "attempt": 0,
        "extent_mm": span.tolist(),
        "lower_area_mm2": polygon_area_xy(xyz[:4]),
        "side_lengths_mm": side_lengths,
        "vertical_pair_gaps_mm": pair_gaps,
        "weirdness_score": float(np.std(side_lengths) + np.std(pair_gaps)),
        "control_5x5": True,
        "constraint": "paired_control_5m_footprint_delta_z_approx_1p4m",
    }
    return xyz, meta


def generate_concave_layout(
    rng: np.random.Generator,
    layout_id: int,
    min_xy_span_mm: float = 0.0,
    min_z_span_mm: float = 0.0,
    max_xy_span_mm: float = 5000.0,
) -> tuple[np.ndarray, dict[str, Any]]:
    for attempt in range(3000):
        width = rng.uniform(0.52 * max_xy_span_mm, 0.98 * max_xy_span_mm)
        depth = rng.uniform(0.42 * max_xy_span_mm, 0.98 * max_xy_span_mm)
        left_y = rng.uniform(0.35, 0.65) * depth
        inner_x = rng.uniform(0.38, 0.68) * width
        inner_y = rng.uniform(0.36, 0.64) * depth
        top_x = rng.uniform(0.72, 1.0) * width
        bot_x = rng.uniform(0.72, 1.0) * width
        top_y = rng.uniform(0.78, 1.0) * depth
        bot_y = rng.uniform(0.0, 0.22) * depth

        lower_xy = np.array(
            [
                [0.0, left_y],        # A: left outside point
                [inner_x, inner_y],   # B: inward/notch point
                [top_x, top_y],       # C: upper-right point
                [bot_x, bot_y],       # D: lower-right point
            ],
            dtype=float,
        )
        lower_xy += rng.normal(0.0, rng.uniform(35.0, 160.0), size=(4, 2))
        lower_xy -= lower_xy[0]
        lower = np.column_stack([lower_xy, rng.normal(0.0, 55.0, size=4)])
        lower[:, 2] -= lower[0, 2]

        dz = rng.normal(-1400.0, 110.0, size=4)
        dz = np.clip(dz, -1700.0, -1100.0)
        lateral = rng.normal(0.0, rng.uniform(80.0, 320.0), size=(4, 2))
        upper_xy = lower[:, :2] + lateral
        upper = np.column_stack([upper_xy, lower[:, 2] + dz])
        xyz = np.vstack([lower, upper])
        xyz = gauge_align_local(normalize_extent_mm(xyz, max_xy_span_mm))

        span = np.max(xyz, axis=0) - np.min(xyz, axis=0)
        if np.max(span[:2]) > max_xy_span_mm + 1e-6 or not passes_extent(xyz, min_xy_span_mm, min_z_span_mm):
            continue
        side_lengths = [np.linalg.norm(xyz[i, :2] - xyz[(i + 1) % 4, :2]) for i in range(4)]
        pair_gaps = [abs(xyz[i + 4, 2] - xyz[i, 2]) for i in range(4)]
        if min(side_lengths) < 650.0 or min(pair_gaps) < 1000.0:
            continue
        if not is_concave_xy(xyz[:4, :2]):
            continue
        area = polygon_area_xy(xyz[:4])
        if area < 600_000.0:
            continue

        notch_depth = float(np.linalg.norm(xyz[1, :2] - 0.5 * (xyz[0, :2] + xyz[2, :2])))
        weirdness = float(np.std(side_lengths) + np.std(pair_gaps) + np.std(xyz[4:, 2]) + notch_depth)
        meta = {
            "layout_id": layout_id,
            "attempt": attempt,
            "extent_mm": span.tolist(),
            "lower_area_mm2": area,
            "side_lengths_mm": side_lengths,
            "vertical_pair_gaps_mm": pair_gaps,
            "weirdness_score": weirdness,
            "notch_depth_mm": notch_depth,
            "concave_lower_xy": True,
            "constraint": "paired_concave_5m_box_delta_z_approx_1p4m",
        }
        return xyz, meta
    raise RuntimeError("failed to generate valid concave layout")


def generate_irregular_layout(
    rng: np.random.Generator,
    layout_id: int,
    geometry_mode: str = "irregular",
    min_xy_span_mm: float = 0.0,
    min_z_span_mm: float = 0.0,
    max_xy_span_mm: float = 5000.0,
) -> tuple[np.ndarray, dict[str, Any]]:
    if geometry_mode == "control5x5":
        return generate_control_5x5_layout(rng, layout_id)
    if geometry_mode == "concave":
        return generate_concave_layout(rng, layout_id, min_xy_span_mm, min_z_span_mm, max_xy_span_mm)
    for attempt in range(2000):
        width = rng.uniform(0.36 * max_xy_span_mm, 0.94 * max_xy_span_mm)
        depth = rng.uniform(0.32 * max_xy_span_mm, 0.94 * max_xy_span_mm)
        base = np.array(
            [
                [0.0, 0.0, 0.0],
                [width, 0.0, 0.0],
                [width, depth, 0.0],
                [0.0, depth, 0.0],
            ],
            dtype=float,
        )

        skew = rng.normal(0.0, rng.uniform(180.0, 720.0), size=(4, 2))
        shear = np.array([[1.0, rng.uniform(-0.45, 0.45)], [rng.uniform(-0.35, 0.35), 1.0]])
        lower_xy = base[:, :2] @ shear.T + skew
        center = np.mean(lower_xy, axis=0)
        order = np.argsort(np.arctan2(lower_xy[:, 1] - center[1], lower_xy[:, 0] - center[0]))
        lower_xy = lower_xy[order]

        # Keep the A/B/C/D logical order around the shape.
        lower_xy -= lower_xy[0]
        lower = np.column_stack([lower_xy, rng.normal(0.0, 45.0, size=4)])
        lower[:, 2] -= lower[0, 2]

        dz = rng.normal(-1400.0, 90.0, size=4)
        dz = np.clip(dz, -1650.0, -1150.0)
        lateral = rng.normal(0.0, rng.uniform(60.0, 260.0), size=(4, 2))
        upper_xy = lower[:, :2] + lateral
        upper = np.column_stack([upper_xy, lower[:, 2] + dz])
        xyz = np.vstack([lower, upper])
        xyz = gauge_align_local(normalize_extent_mm(xyz, max_xy_span_mm))

        span = np.max(xyz, axis=0) - np.min(xyz, axis=0)
        if np.max(span[:2]) > max_xy_span_mm + 1e-6 or not passes_extent(xyz, min_xy_span_mm, min_z_span_mm):
            continue
        side_lengths = [np.linalg.norm(xyz[i, :2] - xyz[(i + 1) % 4, :2]) for i in range(4)]
        pair_gaps = [abs(xyz[i + 4, 2] - xyz[i, 2]) for i in range(4)]
        if min(side_lengths) < 700.0 or min(pair_gaps) < 1000.0:
            continue
        if polygon_area_xy(xyz[:4]) < 1_000_000.0:
            continue

        weirdness = float(np.std(side_lengths) + np.std(pair_gaps) + np.std(xyz[4:, 2]))
        meta = {
            "layout_id": layout_id,
            "attempt": attempt,
            "extent_mm": span.tolist(),
            "lower_area_mm2": polygon_area_xy(xyz[:4]),
            "side_lengths_mm": side_lengths,
            "vertical_pair_gaps_mm": pair_gaps,
            "weirdness_score": weirdness,
            "constraint": "paired_irregular_5m_box_delta_z_approx_1p4m",
        }
        return xyz, meta
    raise RuntimeError("failed to generate valid irregular layout")


def write_layout(path: Path, xyz: np.ndarray, meta: dict[str, Any], solver: str = "truth", d_anchor: np.ndarray | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    d_vals = np.zeros(8, dtype=float) if d_anchor is None else np.asarray(d_anchor, dtype=float)
    data = {
        "solver": solver,
        "anchors": [
            {"id": i, "label": ANCHORS[i], "x_mm": float(xyz[i, 0]), "y_mm": float(xyz[i, 1]), "z_mm": float(xyz[i, 2]), "d_anchor_mm": float(d_vals[i])}
            for i in range(8)
        ],
        "meta": meta,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def simulate_sweep(
    xyz: np.ndarray,
    out_csv: Path,
    rng: np.random.Generator,
    noise_bank: dict[tuple[int, int], np.ndarray],
    sets: int,
) -> dict[str, Any]:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fallback_sigmas = []
    for arr in noise_bank.values():
        fallback_sigmas.append(robust_sigma(arr.tolist(), 8.0))
    fallback_sigma = float(np.median(fallback_sigmas)) if fallback_sigmas else 25.0
    rows_written = 0
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        fields = ["a", "b", "master", "dist_mm", "quality_percent", "raw_mm", "ok", "fail"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for _sweep in range(sets):
            for master in range(8):
                for peer in range(8):
                    if master == peer:
                        continue
                    true_d = float(np.linalg.norm(xyz[master] - xyz[peer]))
                    bank = noise_bank.get((master, peer))
                    if bank is not None and bank.size:
                        err = float(rng.choice(bank))
                    else:
                        err = float(rng.normal(0.0, fallback_sigma))
                    measured = max(100.0, true_d + err)
                    q = 100
                    row = {
                        "a": ANCHORS[min(master, peer)],
                        "b": ANCHORS[max(master, peer)],
                        "master": ANCHORS[master],
                        "dist_mm": int(round(measured)),
                        "quality_percent": q,
                        "raw_mm": int(round(measured)),
                        "ok": 1,
                        "fail": 0,
                    }
                    w.writerow(row)
                    rows_written += 1
    return {"sets": sets, "rows": rows_written, "fallback_sigma_mm": fallback_sigma}


def load_sweep_raw(csv_path: Path) -> dict[tuple[int, int], list[float]]:
    directed: dict[tuple[int, int], list[float]] = defaultdict(list)
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            a = anchor_idx(row["a"])
            b = anchor_idx(row["b"])
            master = anchor_idx(row.get("master") or row["a"])
            d = float(row.get("dist_mm") or row.get("raw_mm"))
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


def kabsch_rms(reference: np.ndarray, estimate: np.ndarray, allow_reflection: bool = True) -> float:
    ref = np.asarray(reference, dtype=float)
    est = np.asarray(estimate, dtype=float)
    ref_c = ref - np.mean(ref, axis=0)
    est_c = est - np.mean(est, axis=0)
    h = est_c.T @ ref_c
    u, _s, vt = np.linalg.svd(h)
    r = u @ vt
    if not allow_reflection and np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    aligned = est_c @ r
    return rms(np.linalg.norm(aligned - ref_c, axis=1))


def pair_residual_rows(xyz: np.ndarray, dly: np.ndarray, fused: dict[tuple[int, int], float]) -> list[dict[str, Any]]:
    rows = []
    for i, j in itertools.combinations(range(8), 2):
        if (i, j) not in fused:
            continue
        pred = float(np.linalg.norm(xyz[i] - xyz[j]) + dly[i] + dly[j])
        err = pred - float(fused[(i, j)])
        rows.append({"pair": f"{ANCHORS[i]}-{ANCHORS[j]}", "pred_mm": pred, "fused_mm": fused[(i, j)], "err_mm": err, "abs_err_mm": abs(err)})
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def solve_one_layout(task: dict[str, Any]) -> list[dict[str, Any]]:
    layout_id = int(task["layout_id"])
    out_root = Path(task["out_root"])
    sets = int(task["sets"])
    seed = int(task["seed"])
    geometry_mode = str(task.get("geometry_mode") or "irregular")
    min_xy_span_mm = float(task.get("min_xy_span_mm") or 0.0)
    min_z_span_mm = float(task.get("min_z_span_mm") or 0.0)
    max_xy_span_mm = float(task.get("max_xy_span_mm") or 5000.0)
    noise_bank = {tuple(map(int, k.split("-"))): np.asarray(v, dtype=float) for k, v in task["noise_bank"].items()}
    mod = load_eval_module()
    rng = np.random.default_rng(seed + layout_id * 10007)
    layout_dir = out_root / f"layout_{layout_id:04d}"
    xyz_true, meta = generate_irregular_layout(rng, layout_id, geometry_mode, min_xy_span_mm, min_z_span_mm, max_xy_span_mm)
    write_layout(layout_dir / "true_layout.json", xyz_true, meta)
    sweep_meta = simulate_sweep(xyz_true, layout_dir / "pairs_all.csv", rng, noise_bank, sets)
    directed = load_sweep_raw(layout_dir / "pairs_all.csv")
    fused = {m: mod.fuse_from_directed(directed, m, list(range(8))) for m in ["v1", "v2", "v3"]}

    rows: list[dict[str, Any]] = []
    for solver_name in SOLVER_NAMES:
        solver_dir = layout_dir / solver_name
        solver_dir.mkdir(parents=True, exist_ok=True)
        main_name = SOLVER_TO_MAIN[solver_name]
        method = "v3" if solver_name in {"v3-lite", "v3-full", "v4-io", "v2"} else "v1"
        if solver_name == "v2":
            method = "v2"
        try:
            if solver_name == "v1-old":
                x, res = mod.solve_autopos_v1(fused["v1"], list(range(8)))
                dly = np.zeros(8, dtype=float)
                extra = {"implementation": "archive_v1_classical_mds_equivalent"}
            else:
                x, dly, res, extra = mod.solver_run(main_name, fused, list(range(8)))
            dly = np.asarray(dly, dtype=float)
            fit_rms = mod.inter_rms_local(x, dly, fused[method], list(range(8)))
            true_pair_errs = []
            for i, j in itertools.combinations(range(8), 2):
                pred = float(np.linalg.norm(x[i] - x[j]))
                truth = float(np.linalg.norm(xyz_true[i] - xyz_true[j]))
                true_pair_errs.append(pred - truth)
            coord_rms = kabsch_rms(xyz_true, x, allow_reflection=True)
            pair_rms_truth = rms(true_pair_errs)
            success = bool(getattr(res, "success", True))
            if solver_name == "v3-full" and isinstance(extra, dict):
                success = bool(extra.get("converged", False))
            residual_rows = pair_residual_rows(x, dly, fused[method])
            write_csv(solver_dir / "layout_residuals.csv", residual_rows)
            write_layout(
                solver_dir / "layout.json",
                x,
                {
                    "layout_id": layout_id,
                    "solver_name": solver_name,
                    "mainline_name": main_name,
                    "success": success,
                    "extra": extra,
                    "d_anchor_mm": dly.tolist(),
                },
                solver=solver_name,
                d_anchor=dly,
            )
            row = {
                "layout_id": layout_id,
                "solver": solver_name,
                "success": success,
                "fit_rms_mm": fit_rms,
                "coord_rms_mm": coord_rms,
                "pair_rms_to_truth_mm": pair_rms_truth,
                "max_pair_abs_to_truth_mm": float(np.max(np.abs(true_pair_errs))),
                "weirdness_score": meta["weirdness_score"],
                "lower_area_mm2": meta["lower_area_mm2"],
                "extent_x_mm": meta["extent_mm"][0],
                "extent_y_mm": meta["extent_mm"][1],
                "extent_z_mm": meta["extent_mm"][2],
                "sweep_rows": sweep_meta["rows"],
                "sets": sets,
                "error": "",
            }
        except Exception as exc:
            row = {
                "layout_id": layout_id,
                "solver": solver_name,
                "success": False,
                "fit_rms_mm": float("nan"),
                "coord_rms_mm": float("nan"),
                "pair_rms_to_truth_mm": float("nan"),
                "max_pair_abs_to_truth_mm": float("nan"),
                "weirdness_score": meta["weirdness_score"],
                "lower_area_mm2": meta["lower_area_mm2"],
                "extent_x_mm": meta["extent_mm"][0],
                "extent_y_mm": meta["extent_mm"][1],
                "extent_z_mm": meta["extent_mm"][2],
                "sweep_rows": sweep_meta["rows"],
                "sets": sets,
                "error": f"{type(exc).__name__}: {exc}",
            }
            (solver_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        rows.append(row)
    return rows


def aggregate_by_solver(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for solver in SOLVER_NAMES:
        group = [r for r in rows if r["solver"] == solver]
        ok = [r for r in group if str(r.get("success")) == "True" or r.get("success") is True]
        entry: dict[str, Any] = {"solver": solver, "n": len(group), "success_n": len(ok), "success_rate": len(ok) / len(group) if group else float("nan")}
        for metric in ["fit_rms_mm", "coord_rms_mm", "pair_rms_to_truth_mm", "max_pair_abs_to_truth_mm"]:
            vals = np.asarray([float(r[metric]) for r in group if np.isfinite(float(r[metric]))], dtype=float)
            if vals.size:
                entry[f"{metric}_median"] = float(np.median(vals))
                entry[f"{metric}_p90"] = float(np.percentile(vals, 90))
                entry[f"{metric}_mean"] = float(np.mean(vals))
            else:
                entry[f"{metric}_median"] = float("nan")
                entry[f"{metric}_p90"] = float("nan")
                entry[f"{metric}_mean"] = float("nan")
        out.append(entry)
    return out


def save_figures(out_root: Path, rows: list[dict[str, Any]]) -> None:
    fig_dir = out_root / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    for metric, name, ylabel in [
        ("coord_rms_mm", "solver_coord_rms_boxplot.png", "Coordinate RMS to truth (mm)"),
        ("fit_rms_mm", "solver_fit_rms_boxplot.png", "Anchor-only fit RMS (mm)"),
    ]:
        data = []
        labels = []
        for solver in SOLVER_NAMES:
            vals = [float(r[metric]) for r in rows if r["solver"] == solver and np.isfinite(float(r[metric]))]
            if vals:
                data.append(vals)
                labels.append(solver)
        if not data:
            continue
        plt.figure(figsize=(9.5, 4.8))
        try:
            plt.boxplot(data, tick_labels=labels, showfliers=False)
        except TypeError:
            plt.boxplot(data, labels=labels, showfliers=False)
        plt.ylabel(ylabel)
        plt.xticks(rotation=20, ha="right")
        plt.tight_layout()
        plt.savefig(fig_dir / name, dpi=160)
        plt.close()


def load_layout_xyz(path: Path) -> np.ndarray:
    raw = json.loads(path.read_text(encoding="utf-8"))
    xyz = np.zeros((8, 3), dtype=float)
    for ent in raw["anchors"]:
        idx = int(ent["id"])
        xyz[idx] = [float(ent["x_mm"]), float(ent["y_mm"]), float(ent["z_mm"])]
    return xyz


def save_layout_gallery(out_root: Path, layouts_per_page: int = 25) -> None:
    fig_dir = out_root / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(out_root.glob("layout_*/true_layout.json"))
    if not paths:
        return

    lower_edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
    upper_edges = [(4, 5), (5, 6), (6, 7), (7, 4)]
    pair_edges = [(0, 4), (1, 5), (2, 6), (3, 7)]
    pages = int(math.ceil(len(paths) / layouts_per_page))

    for page in range(pages):
        chunk = paths[page * layouts_per_page : (page + 1) * layouts_per_page]
        cols = 5
        rows = int(math.ceil(layouts_per_page / cols))
        fig, axes = plt.subplots(rows, cols, figsize=(14.0, 14.0), constrained_layout=True)
        axes_arr = np.asarray(axes).reshape(-1)
        for ax in axes_arr:
            ax.axis("off")
        for ax, path in zip(axes_arr, chunk):
            xyz = load_layout_xyz(path)
            xy = xyz[:, :2] / 1000.0
            center = np.mean(xy, axis=0)
            half = 2.5
            ax.set_xlim(center[0] - half, center[0] + half)
            ax.set_ylim(center[1] - half, center[1] + half)
            ax.set_aspect("equal", adjustable="box")
            ax.grid(True, color="#d8dee6", linewidth=0.5)
            ax.axis("on")
            ax.tick_params(labelsize=6, length=2)
            for i, j in lower_edges:
                ax.plot([xy[i, 0], xy[j, 0]], [xy[i, 1], xy[j, 1]], color="#2563eb", linewidth=1.2)
            for i, j in upper_edges:
                ax.plot([xy[i, 0], xy[j, 0]], [xy[i, 1], xy[j, 1]], color="#dc2626", linewidth=1.2)
            for i, j in pair_edges:
                ax.plot([xy[i, 0], xy[j, 0]], [xy[i, 1], xy[j, 1]], color="#64748b", linewidth=0.8, linestyle="--")
            ax.scatter(xy[:4, 0], xy[:4, 1], s=16, color="#2563eb", zorder=3)
            ax.scatter(xy[4:, 0], xy[4:, 1], s=16, color="#dc2626", zorder=3)
            for idx, label in enumerate(ANCHORS):
                ax.text(xy[idx, 0], xy[idx, 1], label, fontsize=6, ha="center", va="bottom")
            layout_id = path.parent.name.replace("layout_", "")
            ax.set_title(f"layout {layout_id}", fontsize=8)
        fig.suptitle("Synthetic Irregular Anchor Layouts, XY View, 5 m x 5 m Window", fontsize=14)
        fig.savefig(fig_dir / f"layout_xy_gallery_page_{page + 1:02d}.png", dpi=180)
        plt.close(fig)


def save_report(out_root: Path, rows: list[dict[str, Any]], agg: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    lines = [
        "# AutoPos Irregular Layout Simulation Report",
        "",
        f"- layouts: {meta['layouts']}",
        f"- sweep sets per layout: {meta['sets']}",
        f"- solver path: existing mainline CPU/SciPy solvers",
        f"- GPU acceleration: {meta['gpu_acceleration']}",
        f"- reference noise sweep: `{meta['reference_sweep']}`",
        "",
        "## Solver Summary",
        "",
        "| solver | success | fit RMS median | coord RMS median | coord RMS p90 | pair RMS truth median |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in agg:
        lines.append(
            "| {solver} | {success_n}/{n} | {fit:.3f} | {coord:.3f} | {coord90:.3f} | {pair:.3f} |".format(
                solver=row["solver"],
                success_n=row["success_n"],
                n=row["n"],
                fit=float(row["fit_rms_mm_median"]),
                coord=float(row["coord_rms_mm_median"]),
                coord90=float(row["coord_rms_mm_p90"]),
                pair=float(row["pair_rms_to_truth_mm_median"]),
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `fit_rms_mm` is residual against the synthetic fused inter-anchor sweep.",
            "- `coord_rms_mm` is solved coordinates against known truth after rigid alignment with reflection allowed.",
            "- `v4-io` includes the current soft two-layer physical prior, so this report is useful for detecting whether that prior helps or distorts unusual layouts.",
            "",
            "## Files",
            "",
            "- `summary.csv`",
            "- `summary_by_solver.csv`",
            "- `figures/solver_coord_rms_boxplot.png`",
            "- `figures/solver_fit_rms_boxplot.png`",
            "- `figures/layout_xy_gallery_page_*.png`",
        ]
    )
    (out_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Irregular paired-anchor AutoPos simulation.")
    ap.add_argument("--layouts", type=int, default=100, help="number of random layouts")
    ap.add_argument("--start-layout-id", type=int, default=0, help="first layout id to generate")
    ap.add_argument("--geometry-mode", choices=["irregular", "concave", "control5x5"], default="irregular", help="layout generator family")
    ap.add_argument("--min-xy-span-mm", type=float, default=0.0, help="reject generated layouts unless x and y extents are both at least this large")
    ap.add_argument("--min-z-span-mm", type=float, default=0.0, help="reject generated layouts unless z extent is at least this large")
    ap.add_argument("--max-xy-span-mm", type=float, default=5000.0, help="maximum generated x/y footprint span; Phase 2 can use 10000")
    ap.add_argument("--sets", type=int, default=1000, help="synthetic sweep sets per layout")
    ap.add_argument("--workers", type=int, default=max(1, min(8, os.cpu_count() or 1)), help="parallel worker processes")
    ap.add_argument("--seed", type=int, default=20260601)
    ap.add_argument("--out", default="AutoPos_simulation/out_100x1000")
    ap.add_argument("--reference-sweep", default=str(REFERENCE_SWEEP))
    ap.add_argument("--gallery-only", action="store_true", help="only regenerate XY layout gallery from existing true_layout.json files")
    args = ap.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    if args.gallery_only:
        save_layout_gallery(out_root)
        print(f"[sim] wrote {out_root / 'figures'} / layout_xy_gallery_page_*.png", flush=True)
        return 0

    noise = load_reference_noise(Path(args.reference_sweep))
    serializable_noise = {f"{i}-{j}": arr.astype(float).tolist() for (i, j), arr in noise.items()}
    run_meta = {
        "layouts": args.layouts,
        "sets": args.sets,
        "workers": args.workers,
        "seed": args.seed,
        "start_layout_id": args.start_layout_id,
        "geometry_mode": args.geometry_mode,
        "min_xy_span_mm": args.min_xy_span_mm,
        "min_z_span_mm": args.min_z_span_mm,
        "max_xy_span_mm": args.max_xy_span_mm,
        "reference_sweep": str(Path(args.reference_sweep).resolve()),
        "solvers": SOLVER_NAMES,
        "gpu_acceleration": "not_used_scipy_solver_path_cpu_parallel",
    }
    (out_root / "run_meta.json").write_text(json.dumps(run_meta, indent=2), encoding="utf-8")

    tasks = [
        {
            "layout_id": args.start_layout_id + i,
            "out_root": str(out_root),
            "sets": args.sets,
            "seed": args.seed,
            "geometry_mode": args.geometry_mode,
            "min_xy_span_mm": args.min_xy_span_mm,
            "min_z_span_mm": args.min_z_span_mm,
            "max_xy_span_mm": args.max_xy_span_mm,
            "noise_bank": serializable_noise,
        }
        for i in range(args.layouts)
    ]
    all_rows: list[dict[str, Any]] = []
    if args.workers <= 1:
        for task in tasks:
            print(f"[sim] layout {task['layout_id']} ({len(all_rows) // len(SOLVER_NAMES) + 1}/{args.layouts})", flush=True)
            all_rows.extend(solve_one_layout(task))
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(solve_one_layout, task): task for task in tasks}
            done = 0
            for fut in as_completed(futures):
                done += 1
                task = futures[fut]
                print(f"[sim] complete layout {task['layout_id']} ({done}/{args.layouts})", flush=True)
                all_rows.extend(fut.result())

    current_ids = {str(task["layout_id"]) for task in tasks}
    existing_rows = [r for r in read_csv_rows(out_root / "summary.csv") if str(r.get("layout_id")) not in current_ids]
    merged_rows = existing_rows + all_rows
    merged_rows.sort(key=lambda r: (int(r["layout_id"]), SOLVER_NAMES.index(str(r["solver"]))))
    write_csv(out_root / "summary.csv", merged_rows)
    agg = aggregate_by_solver(merged_rows)
    write_csv(out_root / "summary_by_solver.csv", agg)
    save_figures(out_root, merged_rows)
    save_layout_gallery(out_root)
    save_report(out_root, merged_rows, agg, run_meta)
    print(f"[sim] wrote {out_root / 'summary.csv'}", flush=True)
    print(f"[sim] wrote {out_root / 'summary_by_solver.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
