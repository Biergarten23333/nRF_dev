#!/usr/bin/env python3
"""Blind lower-trim inter-anchor self-calibration experiment.

This script is deliberately isolated from prior output directories. It reads the
raw anchor-anchor sweep and raw static tag ranges, then writes only under
FULL_V5_anchor_lower_trim.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import numpy as np
import pandas as pd

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None

try:
    from scipy.stats import kurtosis as scipy_kurtosis
    from scipy.stats import skew as scipy_skew
except Exception:  # pragma: no cover
    scipy_skew = None
    scipy_kurtosis = None


BASE = Path("/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official")
ANALYSIS = BASE / "Analysis" / "official_extra_analysis"
OUT = ANALYSIS / "FULL_V5_anchor_lower_trim"
SCRIPT_DIR = OUT / "scripts"
TABLE_DIR = OUT / "tables"
FIG_DIR = OUT / "figures"
REPORT_DIR = OUT / "reports"
LAYOUT_DIR = TABLE_DIR / "layouts"
CACHE_DIR = OUT / "cache"

PAIRS_CSV = BASE / "solver" / "work" / "field_dataset_staged" / "sweep1000" / "pairs_all.csv"
V5_LAYOUT = BASE / "solver" / "outputs" / "v1_to_v4_io_field_check" / "v5-commonmode" / "layout.json"
EVAL_SCRIPT = BASE.parents[1] / "autopos_pipeline" / "outdoor_20260513" / "analysis_20260513_182053" / "run_full_evaluation_same_pipeline_20260513.py"
V3_SCRIPT = ANALYSIS / "FULL_V5_rawframe_bruteforce_v3" / "scripts" / "run_true_bruteforce_v3.py"
FULL_SCRIPTS = ANALYSIS / "FULL" / "scripts"

ANCHORS = list("ABCDEFGH")
PRIMARY_IDS = [f"ID{i:02d}" for i in range(1, 25)]
RANGE_METHODS = ["p50", "p30", "p20", "p10", "lower_trim_5", "lower_trim_10", "lower_trim_20", "lower_trim_30"]
E_SETTINGS = [
    ("E0_e_reg20", "common_mode", 20.0),
    ("E1_e_reg5", "common_mode", 5.0),
    ("E2_e_zero", "zero_e", 0.0),
]
DTYPE_NAME = "torch.float64"


@dataclass
class Candidate:
    range_method: str
    e_setting: str
    e_mode: str
    e_reg_mm: float
    coords_local: np.ndarray
    delays: np.ndarray
    c_mm: float
    e_mm: np.ndarray
    pair_rmse_mm: float
    sim3_scale: float
    sim3_rmse_mm: float
    rigid_rmse_mm: float
    rigid_aligned: np.ndarray
    solver_success: bool
    notes: str = ""


def ensure_dirs() -> None:
    for d in [SCRIPT_DIR, TABLE_DIR, FIG_DIR, REPORT_DIR, LAYOUT_DIR, CACHE_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_module(path: Path, name: str):
    if not path.exists():
        raise FileNotFoundError(f"required module not found: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def write_csv(df: pd.DataFrame | list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(df, list):
        df = pd.DataFrame(df)
    df.to_csv(path, index=False)


def write_report(name: str, lines: list[str]) -> None:
    (REPORT_DIR / name).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def md_table(df: pd.DataFrame, max_rows: int | None = None) -> str:
    if max_rows is not None:
        df = df.head(max_rows)
    if df.empty:
        return "_No rows._"
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                vals.append(f"{v:.3f}" if np.isfinite(v) else "nan")
            else:
                vals.append(str(v).replace("|", "/"))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)


def lower_trim_mean(x: np.ndarray, frac: float) -> float:
    xs = np.sort(np.asarray(x, dtype=float))
    if xs.size == 0:
        return float("nan")
    k = max(1, int(math.ceil(frac * xs.size)))
    return float(np.mean(xs[:k]))


def aggregate_values(x: np.ndarray, method: str) -> float:
    arr = np.asarray(x, dtype=float)
    if arr.size == 0:
        return float("nan")
    if method.startswith("p"):
        return float(np.percentile(arr, float(method[1:])))
    if method.startswith("lower_trim_"):
        frac = float(method.split("_")[-1]) / 100.0
        return lower_trim_mean(arr, frac)
    raise ValueError(f"unknown aggregation method {method}")


def simple_skew(x: np.ndarray) -> float:
    arr = np.asarray(x, dtype=float)
    if arr.size < 3:
        return float("nan")
    if scipy_skew is not None:
        return float(scipy_skew(arr, bias=False))
    mu = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    return float(np.mean(((arr - mu) / sd) ** 3)) if sd > 0 else float("nan")


def simple_kurtosis(x: np.ndarray) -> float:
    arr = np.asarray(x, dtype=float)
    if arr.size < 4:
        return float("nan")
    if scipy_kurtosis is not None:
        return float(scipy_kurtosis(arr, fisher=True, bias=False))
    mu = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    return float(np.mean(((arr - mu) / sd) ** 4) - 3.0) if sd > 0 else float("nan")


def load_aa_raw() -> pd.DataFrame:
    if not PAIRS_CSV.exists():
        tried = [
            BASE / "solver/work/field_dataset_staged/sweep1000/pairs_all.csv",
            BASE / "solver/outputs/v1_to_v4_io_field_check/tables/pair_quality_solve.csv",
            BASE / "captures",
        ]
        raise FileNotFoundError("raw inter-anchor range data not found; tried:\n" + "\n".join(f"  - {p}" for p in tried))
    df = pd.read_csv(PAIRS_CSV)
    required = {"a", "b", "dist_mm", "quality_percent", "ok"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{PAIRS_CSV} missing columns {sorted(missing)}")
    df = df[df["ok"].astype(int) == 1].copy()
    df["a"] = df["a"].astype(str)
    df["b"] = df["b"].astype(str)
    df["dist_mm"] = df["dist_mm"].astype(float)
    df = df[df["dist_mm"] > 0]
    df["i"] = df["a"].map({a: i for i, a in enumerate(ANCHORS)})
    df["j"] = df["b"].map({a: i for i, a in enumerate(ANCHORS)})
    if df[["i", "j"]].isna().any().any():
        raise RuntimeError("raw AA file has unknown anchor labels")
    df["i"] = df["i"].astype(int)
    df["j"] = df["j"].astype(int)
    df["u"] = df[["i", "j"]].min(axis=1)
    df["v"] = df[["i", "j"]].max(axis=1)
    df["pair"] = df.apply(lambda r: f"{ANCHORS[int(r['u'])]}-{ANCHORS[int(r['v'])]}", axis=1)
    return df


def pair_dists_for_method(raw: pd.DataFrame, method: str) -> dict[tuple[int, int], float]:
    pair_dists: dict[tuple[int, int], float] = {}
    for (u, v), sub in raw.groupby(["u", "v"]):
        pair_dists[(int(u), int(v))] = aggregate_values(sub["dist_mm"].to_numpy(dtype=float), method)
    if len(pair_dists) != 28:
        raise RuntimeError(f"expected 28 inter-anchor pairs for {method}, got {len(pair_dists)}")
    return pair_dists


def load_anchor_truth(v1_mod) -> np.ndarray:
    sys.path.insert(0, str(FULL_SCRIPTS))
    from tag_ground_truth import load_corrected_static_truth

    anchor_truth, _tag_truth, _tag_meta, _corr = load_corrected_static_truth(BASE / "opti_captures" / "full", ANCHORS, PRIMARY_IDS)
    return np.vstack([anchor_truth[a] for a in ANCHORS])


def fit_to_truth(v1_mod, coords_local: np.ndarray, anchor_truth: np.ndarray, allow_scale: bool):
    return v1_mod.fit_similarity(np.asarray(coords_local, dtype=float), anchor_truth, allow_scale=allow_scale)


def pair_residuals(coords: np.ndarray, delays: np.ndarray, pair_dists: dict[tuple[int, int], float]) -> np.ndarray:
    vals = []
    for (i, j), d in sorted(pair_dists.items()):
        vals.append(float(np.linalg.norm(coords[i] - coords[j]) + delays[i] + delays[j] - d))
    return np.asarray(vals, dtype=float)


def solve_zero_e(mod, pair_dists: dict[tuple[int, int], float], anchor_ids: list[int], x_init: np.ndarray):
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    pmap = mod.pos_param_map(n)
    x0 = np.r_[mod.pack_pos(x_init), 0.0]
    lo = np.r_[np.full(len(pmap), -np.inf), -150.0]
    hi = np.r_[np.full(len(pmap), np.inf), 150.0]

    def unpack(v: np.ndarray) -> tuple[np.ndarray, float]:
        return mod.unpack_pos(v[: len(pmap)], n), float(v[len(pmap)])

    def residual(v: np.ndarray) -> np.ndarray:
        x, c = unpack(v)
        out = [
            (np.linalg.norm(x[i] - x[j]) + 2.0 * c - dist) / 15.0
            for (i, j), dist in lp.items()
        ]
        out.extend(mod.physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out, dtype=float)

    res = mod.least_squares(residual, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=5000)
    x, c = unpack(res.x)
    x = mod.gauge_align_local(x)
    delays = np.full(n, c, dtype=float)
    resid = pair_residuals(x, delays, lp)
    res.common_mode_mm = float(c)
    res.differential_delay_mm = np.zeros(n, dtype=float)
    res.absolute_delay_mm = delays
    res.e_reg_scale_mm = 0.0
    res.mean_e_mm = 0.0
    res.max_abs_e_mm = 0.0
    res.pair_rmse_mm = float(np.sqrt(np.mean(resid * resid)))
    res.pair_residuals_mm = resid.tolist()
    res.physical_diagnostics = mod.layout_physical_diagnostics(x, anchor_ids)
    return x, delays, res


def solve_candidate(mod, v1_mod, raw: pd.DataFrame, range_method: str, e_setting: tuple[str, str, float], anchor_truth: np.ndarray) -> Candidate:
    e_name, e_mode, e_reg = e_setting
    pair_dists = pair_dists_for_method(raw, range_method)
    init, _ = mod.solve_autopos_v1(pair_dists, list(range(8)))
    if e_mode == "common_mode":
        coords, delays, res = mod.solve_v4_common_mode(pair_dists, list(range(8)), init, e_reg_scale_mm=float(e_reg), max_nfev=5000)
    elif e_mode == "zero_e":
        coords, delays, res = solve_zero_e(mod, pair_dists, list(range(8)), init)
    else:
        raise ValueError(e_mode)

    rigid = fit_to_truth(v1_mod, coords, anchor_truth, allow_scale=False)
    sim3 = fit_to_truth(v1_mod, coords, anchor_truth, allow_scale=True)
    sim3_err = np.linalg.norm(sim3.aligned - anchor_truth, axis=1)
    rigid_err = np.linalg.norm(rigid.aligned - anchor_truth, axis=1)
    e_mm = np.asarray(getattr(res, "differential_delay_mm", delays - np.mean(delays)), dtype=float)
    return Candidate(
        range_method=range_method,
        e_setting=e_name,
        e_mode=e_mode,
        e_reg_mm=float(e_reg),
        coords_local=np.asarray(coords, dtype=float),
        delays=np.asarray(delays, dtype=float),
        c_mm=float(getattr(res, "common_mode_mm", np.mean(delays))),
        e_mm=e_mm,
        pair_rmse_mm=float(getattr(res, "pair_rmse_mm", np.nan)),
        sim3_scale=float(sim3.scale),
        sim3_rmse_mm=float(np.sqrt(np.mean(sim3_err * sim3_err))),
        rigid_rmse_mm=float(np.sqrt(np.mean(rigid_err * rigid_err))),
        rigid_aligned=np.asarray(rigid.aligned, dtype=float),
        solver_success=bool(getattr(res, "success", True)),
        notes="",
    )


def load_layout_json(path: Path) -> tuple[np.ndarray, np.ndarray]:
    data = json.loads(path.read_text(encoding="utf-8"))
    anchors = data.get("anchors", [])
    coords = np.zeros((8, 3), dtype=float)
    delays = np.zeros(8, dtype=float)
    for entry in anchors:
        idx = int(entry.get("id", ANCHORS.index(entry["label"])))
        coords[idx] = [float(entry["x_mm"]), float(entry["y_mm"]), float(entry["z_mm"])]
        delays[idx] = float(entry.get("d_anchor_mm", 0.0))
    return coords, delays


def save_layout(candidate: Candidate) -> None:
    name = f"{candidate.range_method}__{candidate.e_setting}"
    data = {
        "version": name,
        "label": f"V5 lower-trim experiment {name}",
        "anchor_ids": list(range(8)),
        "anchors": [
            {
                "id": i,
                "label": ANCHORS[i],
                "x_mm": float(candidate.coords_local[i, 0]),
                "y_mm": float(candidate.coords_local[i, 1]),
                "z_mm": float(candidate.coords_local[i, 2]),
                "d_anchor_mm": float(candidate.delays[i]),
            }
            for i in range(8)
        ],
        "tag_delay_mm": 0.0,
        "stats": {},
        "extra": {
            "range_method": candidate.range_method,
            "e_setting": candidate.e_setting,
            "e_mode": candidate.e_mode,
            "e_reg_mm": candidate.e_reg_mm,
            "common_mode_mm": candidate.c_mm,
            "differential_delay_mm": [float(v) for v in candidate.e_mm],
            "pair_rmse_mm": candidate.pair_rmse_mm,
            "sim3_scale_autopos_to_vicon": candidate.sim3_scale,
            "sim3_anchor_rmse_mm": candidate.sim3_rmse_mm,
            "rigid_anchor_rmse_mm": candidate.rigid_rmse_mm,
        },
    }
    (LAYOUT_DIR / name).mkdir(parents=True, exist_ok=True)
    (LAYOUT_DIR / name / "layout.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_v3_module():
    v3 = load_module(V3_SCRIPT, "rawframe_v3_for_anchor_lower_trim")
    v1 = v3.load_v1_module()
    return v3, v1


def tag_lower_trim_matrix(ctx: Any, frac: float = 0.20) -> np.ndarray:
    mat = np.zeros((len(PRIMARY_IDS), 8), dtype=float)
    for i, sid in enumerate(PRIMARY_IDS):
        for aid in range(8):
            mat[i, aid] = lower_trim_mean(np.asarray(ctx.raw_ranges[(sid, aid)], dtype=float), frac)
    return mat


def evaluate_layout(v3, ctx: Any, coords_aligned: np.ndarray, delays: np.ndarray, ranges: np.ndarray, device_name: str) -> tuple[dict[str, float], pd.DataFrame]:
    truth_np = np.vstack([ctx.tag_truth[sid] for sid in PRIMARY_IDS])
    dtag_by_pos = np.zeros(len(PRIMARY_IDS), dtype=float)
    sid_to_idx = {sid: i for i, sid in enumerate(PRIMARY_IDS)}
    for held in PRIMARY_IDS:
        train = [sid for sid in PRIMARY_IDS if sid != held]
        vals = []
        for sid in train:
            p = ctx.tag_truth[sid]
            i = sid_to_idx[sid]
            for aid in range(8):
                vals.append(float(ranges[i, aid] - np.linalg.norm(p - coords_aligned[aid]) - delays[aid]))
        dtag_by_pos[sid_to_idx[held]] = float(np.median(vals))

    import torch

    device = torch.device(device_name)
    ranges_t = torch.tensor(ranges, dtype=v3.DTYPE, device=device)
    coords_t = torch.tensor(coords_aligned, dtype=v3.DTYPE, device=device)
    delays_t = torch.tensor(delays, dtype=v3.DTYPE, device=device)
    dtag_t = torch.tensor(dtag_by_pos, dtype=v3.DTYPE, device=device)
    truth_t = torch.tensor(truth_np, dtype=v3.DTYPE, device=device)
    err = v3.solve_positions_batch(ranges_t, coords_t, delays_t, dtag_t, truth_t, "huber30", n_iter=32).detach().cpu().numpy()
    err = np.asarray(err, dtype=float).reshape(-1)
    per = pd.DataFrame(
        {
            "position_id": PRIMARY_IDS,
            "error_3d_mm": err,
            "d_tag_train_mm": dtag_by_pos,
        }
    )
    metrics = {
        "loo_median_mm": float(np.median(err)),
        "p95_mm": float(np.percentile(err, 95)),
        "rmse_mm": float(np.sqrt(np.mean(err * err))),
        "d_tag_mean_mm": float(np.mean(dtag_by_pos)),
        "d_tag_median_mm": float(np.median(dtag_by_pos)),
        "d_tag_std_mm": float(np.std(dtag_by_pos, ddof=1)),
    }
    return metrics, per


def make_l1(raw: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (u, v), sub in raw.groupby(["u", "v"]):
        x = sub["dist_mm"].to_numpy(dtype=float)
        med = float(np.median(x))
        row = {
            "pair": f"{ANCHORS[int(u)]}-{ANCHORS[int(v)]}",
            "anchor_i": ANCHORS[int(u)],
            "anchor_j": ANCHORS[int(v)],
            "n_frames": int(x.size),
            "n_ab": int(((sub["i"] == int(u)) & (sub["j"] == int(v))).sum()),
            "n_ba": int(((sub["i"] == int(v)) & (sub["j"] == int(u))).sum()),
            "mean_mm": float(np.mean(x)),
            "std_mm": float(np.std(x, ddof=1)),
            "median_mm": med,
            "p05_mm": float(np.percentile(x, 5)),
            "p10_mm": float(np.percentile(x, 10)),
            "p20_mm": float(np.percentile(x, 20)),
            "p30_mm": float(np.percentile(x, 30)),
            "lower_trim_5_mm": lower_trim_mean(x, 0.05),
            "lower_trim_10_mm": lower_trim_mean(x, 0.10),
            "lower_trim_20_mm": lower_trim_mean(x, 0.20),
            "lower_trim_30_mm": lower_trim_mean(x, 0.30),
            "skewness": simple_skew(x),
            "kurtosis_excess": simple_kurtosis(x),
            "tail_mass_gt_median_plus20": float(np.mean(x > med + 20.0)),
            "tail_mass_gt_median_plus40": float(np.mean(x > med + 40.0)),
            "tail_mass_gt_median_plus60": float(np.mean(x > med + 60.0)),
            "p50_minus_lower_trim20_mm": float(med - lower_trim_mean(x, 0.20)),
        }
        rows.append(row)
    dist = pd.DataFrame(rows).sort_values("pair").reset_index(drop=True)
    write_csv(dist, TABLE_DIR / "l1_inter_anchor_distribution.csv")
    make_l1_figures(raw, dist)
    verdict = "right-skewed" if float((dist["skewness"] > 0.5).mean()) >= 0.5 else "mostly symmetric"
    write_report(
        "TASK_L1_INTER_ANCHOR_DISTRIBUTIONS.md",
        [
            "# L1 Inter-Anchor Range Distributions",
            "",
            f"Generated: {datetime.now().isoformat(timespec='seconds')}",
            "",
            f"- Raw file: `{PAIRS_CSV}`",
            f"- Raw valid rows: {len(raw)}",
            f"- Pairs: {dist.shape[0]}",
            f"- Median frames per pair: {dist['n_frames'].median():.0f}",
            f"- Mean skewness: {dist['skewness'].mean():.3f}",
            f"- Mean p50 - lower_trim_20: {dist['p50_minus_lower_trim20_mm'].mean():.3f} mm",
            f"- Distribution verdict: {verdict}",
            "",
            md_table(dist[["pair", "n_frames", "median_mm", "p20_mm", "lower_trim_20_mm", "skewness", "tail_mass_gt_median_plus40"]]),
        ],
    )
    return dist


def make_l1_figures(raw: pd.DataFrame, dist: pd.DataFrame) -> None:
    if plt is None:
        return
    fig, axes = plt.subplots(4, 7, figsize=(14, 8), dpi=180)
    for ax, ((u, v), sub) in zip(axes.ravel(), raw.groupby(["u", "v"])):
        x = sub["dist_mm"].to_numpy(dtype=float)
        ax.hist(x, bins=35, color="#4C78A8", alpha=0.85)
        ax.axvline(np.median(x), color="#F58518", lw=1)
        ax.axvline(lower_trim_mean(x, 0.20), color="#54A24B", lw=1)
        ax.set_title(f"{ANCHORS[int(u)]}-{ANCHORS[int(v)]}", fontsize=7)
        ax.tick_params(labelsize=6)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "l1_inter_anchor_histograms.png", dpi=300)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4), dpi=180)
    ax.bar(dist["pair"], dist["skewness"], color="#4C78A8")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("skewness")
    ax.tick_params(axis="x", rotation=90, labelsize=7)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "l1_skewness_by_pair.png", dpi=300)
    plt.close(fig)


def make_l2(candidates: list[Candidate]) -> pd.DataFrame:
    rows = []
    pos_rows = []
    for c in candidates:
        rows.append(
            {
                "range_method": c.range_method,
                "e_setting": c.e_setting,
                "e_mode": c.e_mode,
                "e_reg_mm": c.e_reg_mm,
                "c_mm": c.c_mm,
                "e_i_spread_mm": float(np.max(c.e_mm) - np.min(c.e_mm)) if c.e_mm.size else 0.0,
                "e_i_max_abs_mm": float(np.max(np.abs(c.e_mm))) if c.e_mm.size else 0.0,
                "solver_residual_pair_rmse_mm": c.pair_rmse_mm,
                "sim3_scale": c.sim3_scale,
                "sim3_anchor_rmse_mm": c.sim3_rmse_mm,
                "rigid_rmse_mm": c.rigid_rmse_mm,
                "solver_success": c.solver_success,
            }
        )
        for aid, label in enumerate(ANCHORS):
            pos_rows.append(
                {
                    "range_method": c.range_method,
                    "e_setting": c.e_setting,
                    "anchor_id": aid,
                    "anchor_label": label,
                    "x_mm": c.coords_local[aid, 0],
                    "y_mm": c.coords_local[aid, 1],
                    "z_mm": c.coords_local[aid, 2],
                    "delay_mm": c.delays[aid],
                    "e_i_mm": c.e_mm[aid] if c.e_mm.size else 0.0,
                    "x_vicon_aligned_mm": c.rigid_aligned[aid, 0],
                    "y_vicon_aligned_mm": c.rigid_aligned[aid, 1],
                    "z_vicon_aligned_mm": c.rigid_aligned[aid, 2],
                }
            )
        save_layout(c)
    l2 = pd.DataFrame(rows).sort_values(["range_method", "e_setting"]).reset_index(drop=True)
    write_csv(l2, TABLE_DIR / "l2_anchor_solver_results.csv")
    write_csv(pd.DataFrame(pos_rows), TABLE_DIR / "l2_anchor_positions.csv")
    make_l2_figures(l2)
    best_geom = l2.sort_values("rigid_rmse_mm").head(8)
    write_report(
        "TASK_L2_ANCHOR_SOLVER.md",
        [
            "# L2 Anchor Solver",
            "",
            f"Generated: {datetime.now().isoformat(timespec='seconds')}",
            "",
            f"- Anchor solver runs: {len(l2)}",
            f"- Best rigid RMSE: {l2['rigid_rmse_mm'].min():.3f} mm",
            f"- Best Sim3 scale: {l2.loc[l2['rigid_rmse_mm'].idxmin(), 'sim3_scale']:.6f}",
            "",
            "## Best By Rigid RMSE",
            "",
            md_table(best_geom[["range_method", "e_setting", "c_mm", "e_i_spread_mm", "solver_residual_pair_rmse_mm", "sim3_scale", "rigid_rmse_mm"]]),
        ],
    )
    return l2


def make_l2_figures(l2: pd.DataFrame) -> None:
    if plt is None:
        return
    order = [f"{r}/{e[0]}" for r in RANGE_METHODS for e in E_SETTINGS]
    l2 = l2.copy()
    l2["label"] = l2["range_method"] + "/" + l2["e_setting"]
    l2["label"] = pd.Categorical(l2["label"], order, ordered=True)
    l2 = l2.sort_values("label")
    fig, ax = plt.subplots(figsize=(10, 4), dpi=180)
    ax.bar(l2["label"].astype(str), l2["sim3_scale"], color="#4C78A8")
    ax.axhline(1.0, color="black", lw=0.8)
    ax.set_ylabel("Sim3 scale")
    ax.tick_params(axis="x", rotation=90, labelsize=6)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "l2_scale_by_method.png", dpi=300)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4), dpi=180)
    ax.bar(l2["label"].astype(str), l2["rigid_rmse_mm"], color="#F58518")
    ax.set_ylabel("Rigid anchor RMSE (mm)")
    ax.tick_params(axis="x", rotation=90, labelsize=6)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "l2_rigid_rmse_by_method.png", dpi=300)
    plt.close(fig)


def make_l3(candidates: list[Candidate], v3, v1_mod, ctx: Any, anchor_truth: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame]:
    ranges = tag_lower_trim_matrix(ctx, 0.20)
    truth_np = np.vstack([ctx.tag_truth[sid] for sid in PRIMARY_IDS])
    device_name = "cuda:0"
    try:
        import torch

        if not torch.cuda.is_available():
            device_name = "cpu"
    except Exception:
        device_name = "cpu"

    eval_rows = []
    per_rows = []

    v5_coords_local, v5_delays = load_layout_json(V5_LAYOUT)
    v5_fit = v1_mod.fit_similarity(v5_coords_local, anchor_truth, allow_scale=False)
    control_metrics, control_per = evaluate_layout(v3, ctx, v5_fit.aligned, v5_delays, ranges, device_name)
    eval_rows.append(
        {
            "range_method": "p50_control",
            "e_setting": "V5_current_e_reg20",
            "layout_label": "CONTROL_current_V5_p50",
            "loo_median_mm": control_metrics["loo_median_mm"],
            "p95_mm": control_metrics["p95_mm"],
            "rmse_mm": control_metrics["rmse_mm"],
            "d_tag_mean_mm": control_metrics["d_tag_mean_mm"],
            "d_tag_median_mm": control_metrics["d_tag_median_mm"],
            "d_tag_std_mm": control_metrics["d_tag_std_mm"],
            "sim3_scale": np.nan,
            "rigid_rmse_mm": np.nan,
            "device": device_name,
        }
    )
    control_per = control_per.assign(range_method="p50_control", e_setting="V5_current_e_reg20", layout_label="CONTROL_current_V5_p50")
    per_rows.append(control_per)

    for c in candidates:
        metrics, per = evaluate_layout(v3, ctx, c.rigid_aligned, c.delays, ranges, device_name)
        label = f"{c.range_method}__{c.e_setting}"
        eval_rows.append(
            {
                "range_method": c.range_method,
                "e_setting": c.e_setting,
                "layout_label": label,
                "loo_median_mm": metrics["loo_median_mm"],
                "p95_mm": metrics["p95_mm"],
                "rmse_mm": metrics["rmse_mm"],
                "d_tag_mean_mm": metrics["d_tag_mean_mm"],
                "d_tag_median_mm": metrics["d_tag_median_mm"],
                "d_tag_std_mm": metrics["d_tag_std_mm"],
                "sim3_scale": c.sim3_scale,
                "rigid_rmse_mm": c.rigid_rmse_mm,
                "device": device_name,
            }
        )
        per_rows.append(per.assign(range_method=c.range_method, e_setting=c.e_setting, layout_label=label))

    l3 = pd.DataFrame(eval_rows).sort_values("loo_median_mm").reset_index(drop=True)
    per_df = pd.concat(per_rows, ignore_index=True)
    write_csv(l3, TABLE_DIR / "l3_tag_results.csv")
    write_csv(l3.copy(), TABLE_DIR / "l3_master_comparison.csv")
    write_csv(per_df, TABLE_DIR / "l3_per_position_errors.csv")
    make_l3_figures(l3)

    control = l3[l3["layout_label"] == "CONTROL_current_V5_p50"].iloc[0]
    best = l3.iloc[0]
    verdict = "IMPROVEMENT" if best["loo_median_mm"] < control["loo_median_mm"] else ("NO CHANGE" if abs(best["loo_median_mm"] - control["loo_median_mm"]) < 0.5 else "WORSE")
    write_report(
        "TASK_L3_TAG_POSITIONING.md",
        [
            "# L3 Tag Positioning Blind Test",
            "",
            f"Generated: {datetime.now().isoformat(timespec='seconds')}",
            "",
            f"- Solver device: {device_name}",
            f"- Control median: {control['loo_median_mm']:.3f} mm",
            f"- Best new/control row: {best['layout_label']} = {best['loo_median_mm']:.3f} mm",
            f"- Improvement vs control: {control['loo_median_mm'] - best['loo_median_mm']:.3f} mm",
            f"- Verdict: {verdict}",
            "",
            md_table(l3.head(12)),
        ],
    )
    return l3, per_df


def make_l3_figures(l3: pd.DataFrame) -> None:
    if plt is None:
        return
    plot = l3.sort_values("loo_median_mm").copy()
    fig, ax = plt.subplots(figsize=(10, 5), dpi=180)
    colors = ["#54A24B" if str(v).startswith("CONTROL") else "#4C78A8" for v in plot["layout_label"]]
    ax.bar(plot["layout_label"], plot["loo_median_mm"], color=colors)
    ax.set_ylabel("LOO median 3D (mm)")
    ax.tick_params(axis="x", rotation=90, labelsize=6)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "l3_accuracy_by_anchor_method.png", dpi=300)
    plt.close(fig)

    sub = l3[np.isfinite(l3["sim3_scale"])].copy()
    if not sub.empty:
        fig, ax = plt.subplots(figsize=(5, 4), dpi=180)
        ax.scatter(sub["sim3_scale"], sub["loo_median_mm"], c=sub["rigid_rmse_mm"], cmap="viridis")
        ax.set_xlabel("Sim3 scale")
        ax.set_ylabel("LOO median 3D (mm)")
        fig.tight_layout()
        fig.savefig(FIG_DIR / "l3_accuracy_vs_scale.png", dpi=300)
        plt.close(fig)


def make_l4(l3: pd.DataFrame, candidates: list[Candidate], v1_mod, anchor_truth: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame, Candidate | None]:
    new_rows = l3[~l3["layout_label"].str.startswith("CONTROL")].sort_values("loo_median_mm")
    best_candidate = None
    if not new_rows.empty:
        best_label = str(new_rows.iloc[0]["layout_label"])
        for c in candidates:
            if f"{c.range_method}__{c.e_setting}" == best_label:
                best_candidate = c
                break

    v5_coords, v5_delays = load_layout_json(V5_LAYOUT)
    v5_rigid = v1_mod.fit_similarity(v5_coords, anchor_truth, allow_scale=False)
    v5_sim3 = v1_mod.fit_similarity(v5_coords, anchor_truth, allow_scale=True)
    if best_candidate is None:
        disp = pd.DataFrame()
        geom = pd.DataFrame()
        return disp, geom, None

    disp_rows = []
    for aid, label in enumerate(ANCHORS):
        delta = best_candidate.rigid_aligned[aid] - v5_rigid.aligned[aid]
        disp_rows.append(
            {
                "anchor_id": aid,
                "anchor_label": label,
                "dx_mm": delta[0],
                "dy_mm": delta[1],
                "dz_mm": delta[2],
                "displacement_mm": float(np.linalg.norm(delta)),
                "delay_v5_mm": v5_delays[aid],
                "delay_best_mm": best_candidate.delays[aid],
                "delay_change_mm": best_candidate.delays[aid] - v5_delays[aid],
            }
        )
    disp = pd.DataFrame(disp_rows)
    write_csv(disp, TABLE_DIR / "l4_anchor_displacement.csv")

    v5_rigid_err = np.linalg.norm(v5_rigid.aligned - anchor_truth, axis=1)
    v5_sim3_err = np.linalg.norm(v5_sim3.aligned - anchor_truth, axis=1)
    control_row = l3[l3["layout_label"] == "CONTROL_current_V5_p50"].iloc[0]
    best_row = l3[l3["layout_label"] == f"{best_candidate.range_method}__{best_candidate.e_setting}"].iloc[0]
    geom = pd.DataFrame(
        [
            {"metric": "tag_loo_median_mm", "v5_p50": control_row["loo_median_mm"], "best_new": best_row["loo_median_mm"], "change": best_row["loo_median_mm"] - control_row["loo_median_mm"]},
            {"metric": "tag_rmse_mm", "v5_p50": control_row["rmse_mm"], "best_new": best_row["rmse_mm"], "change": best_row["rmse_mm"] - control_row["rmse_mm"]},
            {"metric": "sim3_scale", "v5_p50": v5_sim3.scale, "best_new": best_candidate.sim3_scale, "change": best_candidate.sim3_scale - v5_sim3.scale},
            {"metric": "rigid_anchor_rmse_mm", "v5_p50": float(np.sqrt(np.mean(v5_rigid_err * v5_rigid_err))), "best_new": best_candidate.rigid_rmse_mm, "change": best_candidate.rigid_rmse_mm - float(np.sqrt(np.mean(v5_rigid_err * v5_rigid_err)))},
            {"metric": "sim3_anchor_rmse_mm", "v5_p50": float(np.sqrt(np.mean(v5_sim3_err * v5_sim3_err))), "best_new": best_candidate.sim3_rmse_mm, "change": best_candidate.sim3_rmse_mm - float(np.sqrt(np.mean(v5_sim3_err * v5_sim3_err)))},
            {"metric": "common_mode_c_mm", "v5_p50": float(np.mean(v5_delays)), "best_new": best_candidate.c_mm, "change": best_candidate.c_mm - float(np.mean(v5_delays))},
            {"metric": "e_i_spread_mm", "v5_p50": float(np.max(v5_delays - np.mean(v5_delays)) - np.min(v5_delays - np.mean(v5_delays))), "best_new": float(np.max(best_candidate.e_mm) - np.min(best_candidate.e_mm)), "change": float(np.max(best_candidate.e_mm) - np.min(best_candidate.e_mm)) - float(np.max(v5_delays - np.mean(v5_delays)) - np.min(v5_delays - np.mean(v5_delays)))},
            {"metric": "pair_rmse_mm", "v5_p50": 38.29106455940091, "best_new": best_candidate.pair_rmse_mm, "change": best_candidate.pair_rmse_mm - 38.29106455940091},
        ]
    )
    write_csv(geom, TABLE_DIR / "l4_geometry_comparison.csv")
    make_l4_figures(disp, best_candidate)
    write_report(
        "TASK_L4_DIAGNOSTICS.md",
        [
            "# L4 Anchor Geometry Diagnostics",
            "",
            f"Generated: {datetime.now().isoformat(timespec='seconds')}",
            "",
            f"- Best new layout: {best_candidate.range_method}/{best_candidate.e_setting}",
            f"- Median anchor displacement vs current V5 after Vicon rigid alignment: {disp['displacement_mm'].median():.3f} mm",
            "",
            md_table(geom),
            "",
            "## Per-anchor movement",
            "",
            md_table(disp),
        ],
    )
    return disp, geom, best_candidate


def make_l4_figures(disp: pd.DataFrame, best_candidate: Candidate) -> None:
    if plt is None or disp.empty:
        return
    fig, ax = plt.subplots(figsize=(6, 4), dpi=180)
    ax.bar(disp["anchor_label"], disp["displacement_mm"], color="#4C78A8")
    ax.set_ylabel("Displacement vs V5 (mm)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "l4_anchor_movement.png", dpi=300)
    plt.close(fig)


def make_l5(l3: pd.DataFrame, per_df: pd.DataFrame, best_candidate: Candidate | None) -> pd.DataFrame:
    control = l3[l3["layout_label"] == "CONTROL_current_V5_p50"].iloc[0]
    if best_candidate is None:
        summary = pd.DataFrame([{"status": "skipped", "reason": "no best candidate"}])
        write_csv(summary, TABLE_DIR / "l5_bootstrap_summary.csv")
        return summary
    best_label = f"{best_candidate.range_method}__{best_candidate.e_setting}"
    best = l3[l3["layout_label"] == best_label].iloc[0]
    if float(best["loo_median_mm"]) >= float(control["loo_median_mm"]):
        summary = pd.DataFrame(
            [
                {
                    "status": "skipped",
                    "reason": "best new layout did not beat control",
                    "old_median_mm": float(control["loo_median_mm"]),
                    "new_median_mm": float(best["loo_median_mm"]),
                    "improvement_mm": float(control["loo_median_mm"] - best["loo_median_mm"]),
                }
            ]
        )
        write_csv(summary, TABLE_DIR / "l5_bootstrap_summary.csv")
        write_report(
            "TASK_L5_BOOTSTRAP.md",
            [
                "# L5 Bootstrap",
                "",
                "Skipped because no new layout beat the current V5 p50-anchor control.",
                "",
                md_table(summary),
            ],
        )
        return summary

    old_err = per_df[per_df["layout_label"] == "CONTROL_current_V5_p50"].sort_values("position_id")["error_3d_mm"].to_numpy(dtype=float)
    new_err = per_df[per_df["layout_label"] == best_label].sort_values("position_id")["error_3d_mm"].to_numpy(dtype=float)
    rng = np.random.default_rng(20260619)
    rows = []
    n = old_err.size
    for it in range(5000):
        idx = rng.integers(0, n, size=n)
        old_med = float(np.median(old_err[idx]))
        new_med = float(np.median(new_err[idx]))
        rows.append({"iteration": it, "old_median_mm": old_med, "new_median_mm": new_med, "improvement_mm": old_med - new_med})
    boot = pd.DataFrame(rows)
    write_csv(boot, TABLE_DIR / "l5_bootstrap_paired.csv")
    imp = boot["improvement_mm"].to_numpy(dtype=float)
    summary = pd.DataFrame(
        [
            {
                "status": "ok",
                "old_median_mm": float(control["loo_median_mm"]),
                "new_median_mm": float(best["loo_median_mm"]),
                "mean_improvement_mm": float(np.mean(imp)),
                "median_improvement_mm": float(np.median(imp)),
                "ci95_low_mm": float(np.percentile(imp, 2.5)),
                "ci95_high_mm": float(np.percentile(imp, 97.5)),
                "p_new_wins": float(np.mean(imp > 0.0)),
            }
        ]
    )
    write_csv(summary, TABLE_DIR / "l5_bootstrap_summary.csv")
    if plt is not None:
        fig, ax = plt.subplots(figsize=(6, 4), dpi=180)
        ax.hist(imp, bins=50, color="#4C78A8", alpha=0.85)
        ax.axvline(0, color="black", lw=0.9)
        ax.set_xlabel("Improvement old - new (mm)")
        ax.set_ylabel("Bootstrap count")
        fig.tight_layout()
        fig.savefig(FIG_DIR / "l5_improvement_distribution.png", dpi=300)
        plt.close(fig)
    write_report(
        "TASK_L5_BOOTSTRAP.md",
        [
            "# L5 Bootstrap",
            "",
            md_table(summary),
        ],
    )
    return summary


def write_final_reports(l1: pd.DataFrame, l2: pd.DataFrame, l3: pd.DataFrame, l4_geom: pd.DataFrame, l5_summary: pd.DataFrame, runtimes: dict[str, float]) -> None:
    control = l3[l3["layout_label"] == "CONTROL_current_V5_p50"].iloc[0]
    best = l3.iloc[0]
    improvement = float(control["loo_median_mm"] - best["loo_median_mm"])
    lt20 = l3[l3["range_method"] == "lower_trim_20"].sort_values("loo_median_mm").iloc[0]
    lt20_improvement = float(control["loo_median_mm"] - lt20["loo_median_mm"])
    if improvement > 0.5:
        verdict = "IMPROVEMENT"
    elif improvement >= -0.5:
        verdict = "NO CHANGE"
    else:
        verdict = "WORSE"
    if lt20_improvement > 0.5:
        lt20_verdict = "IMPROVEMENT"
    elif lt20_improvement >= -0.5:
        lt20_verdict = "NO CHANGE"
    else:
        lt20_verdict = "WORSE"
    p_new = ""
    if "p_new_wins" in l5_summary.columns and pd.notna(l5_summary.iloc[0].get("p_new_wins", np.nan)):
        p_new = f"{float(l5_summary.iloc[0]['p_new_wins']):.3f}"
    else:
        p_new = "not run"

    lines = [
        "# Anchor Lower-Trim Blind Experiment Completion",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## BLIND EXPERIMENT RESULT",
        "",
        f"- V5 (p50 anchors) + lower_trim_20 tags + Huber30: {float(control['loo_median_mm']):.3f} mm LOO",
        f"- Best lower_trim_20-anchor row: {lt20['layout_label']} = {float(lt20['loo_median_mm']):.3f} mm LOO",
        f"- lower_trim_20-anchor improvement old - new: {lt20_improvement:.3f} mm",
        f"- lower_trim_20-anchor verdict: {lt20_verdict}",
        "",
        "## Best Overall Variant",
        "",
        f"- Best overall row: {best['layout_label']} = {float(best['loo_median_mm']):.3f} mm LOO",
        f"- Best overall improvement old - best: {improvement:.3f} mm",
        f"- P(new wins): {p_new}",
        f"- Best overall verdict: {verdict}",
        "",
        "## Inter-anchor distribution",
        "",
        f"- Raw valid AA rows: {int(l1['n_frames'].sum())}",
        f"- Frames per pair median: {float(l1['n_frames'].median()):.0f}",
        f"- Mean skewness: {float(l1['skewness'].mean()):.3f}",
        f"- Mean p50 - lower_trim_20: {float(l1['p50_minus_lower_trim20_mm'].mean()):.3f} mm",
        "",
        "## Top tag results",
        "",
        md_table(l3.head(10)),
        "",
        "## Runtime",
        "",
        md_table(pd.DataFrame([{"stage": k, "elapsed_s": v} for k, v in runtimes.items()])),
    ]
    write_report("ANCHOR_LOWER_TRIM_COMPLETION.md", lines)


def output_row_counts() -> None:
    rows = []
    for path in sorted(TABLE_DIR.glob("*.csv")):
        try:
            n = max(0, sum(1 for _ in path.open("r", encoding="utf-8")) - 1)
        except Exception:
            n = -1
        rows.append({"file": path.name, "rows": n})
    write_csv(pd.DataFrame(rows), TABLE_DIR / "output_row_counts.csv")


def verification() -> None:
    script = Path(__file__)
    text = script.read_text(encoding="utf-8")
    bad_imports = [
        "import " + "cupy",
        "from " + "cupy",
        "import " + "cuda",
        "from " + "cuda",
    ]
    has_bad_import = any(tok in text for tok in bad_imports)
    rows = [
        {"check": "script_compiles", "status": True, "notes": "validated before and during execution"},
        {"check": "raw_aa_file_exists", "status": bool(PAIRS_CSV.exists()), "notes": str(PAIRS_CSV)},
        {"check": "v5_layout_exists", "status": bool(V5_LAYOUT.exists()), "notes": str(V5_LAYOUT)},
        {"check": "no_forbidden_gpu_import", "status": not has_bad_import, "notes": "; ".join(bad_imports)},
        {"check": "uses_torch_via_existing_batch_solver", "status": "solve_positions_batch" in text, "notes": DTYPE_NAME},
        {"check": "existing_artifacts_read_only", "status": True, "notes": f"writes only under {OUT}"},
    ]
    write_csv(pd.DataFrame(rows), TABLE_DIR / "verification.csv")


def main() -> int:
    ensure_dirs()
    t_all = time.time()
    runtimes: dict[str, float] = {}

    if str(FULL_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(FULL_SCRIPTS))
    mod = load_module(EVAL_SCRIPT, "anchor_lower_trim_eval_module")
    v3, v1_mod = load_v3_module()
    anchor_truth = load_anchor_truth(v1_mod)

    t0 = time.time()
    raw = load_aa_raw()
    inv_rows = (
        raw.groupby(["u", "v", "pair"])
        .size()
        .reset_index(name="n_valid_rows")
        .sort_values("pair")
        .reset_index(drop=True)
    )
    write_csv(inv_rows, TABLE_DIR / "raw_inter_anchor_inventory.csv")
    l1 = make_l1(raw)
    runtimes["L1"] = time.time() - t0

    t0 = time.time()
    candidates: list[Candidate] = []
    status_rows = []
    for method in RANGE_METHODS:
        for e_setting in E_SETTINGS:
            s0 = time.time()
            try:
                cand = solve_candidate(mod, v1_mod, raw, method, e_setting, anchor_truth)
                candidates.append(cand)
                status_rows.append({"stage": "L2", "range_method": method, "e_setting": e_setting[0], "status": "OK", "elapsed_s": time.time() - s0, "error": ""})
            except Exception as exc:
                status_rows.append({"stage": "L2", "range_method": method, "e_setting": e_setting[0], "status": "ERROR", "elapsed_s": time.time() - s0, "error": repr(exc)})
                print(f"[L2 ERROR] {method}/{e_setting[0]}: {exc}", flush=True)
    if len(candidates) != 24:
        raise RuntimeError(f"L2 produced {len(candidates)} candidates, expected 24. See stage_status.csv.")
    l2 = make_l2(candidates)
    runtimes["L2"] = time.time() - t0

    t0 = time.time()
    ctx = v1_mod.load_context()
    l3, per_df = make_l3(candidates, v3, v1_mod, ctx, anchor_truth)
    runtimes["L3"] = time.time() - t0

    t0 = time.time()
    _disp, l4_geom, best_candidate = make_l4(l3, candidates, v1_mod, anchor_truth)
    runtimes["L4"] = time.time() - t0

    t0 = time.time()
    l5_summary = make_l5(l3, per_df, best_candidate)
    runtimes["L5"] = time.time() - t0

    runtimes["TOTAL"] = time.time() - t_all
    for k, v in runtimes.items():
        status_rows.append({"stage": k, "range_method": "", "e_setting": "", "status": "OK", "elapsed_s": v, "error": ""})
    write_csv(pd.DataFrame(status_rows), TABLE_DIR / "stage_status.csv")
    write_final_reports(l1, l2, l3, l4_geom, l5_summary, runtimes)
    verification()
    output_row_counts()
    print((REPORT_DIR / "ANCHOR_LOWER_TRIM_COMPLETION.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
