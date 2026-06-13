#!/usr/bin/env python3
"""Revised Phase-1 audit for the Measurement review response.

Outputs stay under official_extra_analysis/FULL/audit_phase1.  The script does
not edit the paper; it only writes audit tables, relaxed-bound layouts, and a
machine-readable summary consumed by AUDIT_FINDINGS.md.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.stats import t as student_t


ANCHORS = list("ABCDEFGH")
N_MC = 1000
RNG_SEED = 20260611

THIS = Path(__file__).resolve()
FULL_ROOT = THIS.parents[1]
ANALYSIS_ROOT = THIS.parents[3]
OFFICIAL_ROOT = THIS.parents[4]
REPO_ROOT = OFFICIAL_ROOT.parents[1]
OUT = FULL_ROOT / "audit_phase1"
TABLES = OUT / "tables"
FIGS = OUT / "figs"
LAYOUTS = OUT / "layouts"

RUN_CLEAN = (
    REPO_ROOT
    / "biospur_tag_positioning_offline_solver"
    / "reference_current_implementations"
    / "official_report_field_solver_13052026"
    / "run_clean_full_compare.py"
)
STATIC_REPLAY = FULL_ROOT / "scripts" / "static_tag_raw_replay_matrix.py"
CURRENT_LAYOUT = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json"
CURRENT_LAYOUT_DIR = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check"
CURRENT_SIGMA = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check/tables/anchor_sigma.json"
LAYOUT_ABS = FULL_ROOT / "tables/layout_abs_errors_all8.csv"
CURRENT_T4_STATIC = (
    OFFICIAL_ROOT
    / "solver/work/field_dataset_staged/FULL-COMPARE-1000-production-T4-real/tables/static_all_captures.csv"
)
CURRENT_T4_ACCURACY = (
    ANALYSIS_ROOT
    / "official_extra_analysis/FULL_4way_comparison/production_method_probe/production_static_method_real_run_eval/tables/tag_accuracy_summary.csv"
)
CURRENT_T4_SESSION = (
    ANALYSIS_ROOT
    / "official_extra_analysis/FULL_4way_comparison/production_method_probe/production_static_method_real_run_eval/tables/tag_abs_errors_per_session.csv"
)
STAGED = OFFICIAL_ROOT / "solver/work/field_dataset_staged"


@dataclass
class FitResult:
    aligned: np.ndarray
    rotation: np.ndarray
    translation: np.ndarray
    scale: float
    det: float


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_static_replay_module():
    sys.path.insert(0, str(FULL_ROOT / "scripts"))
    sys.path.insert(0, str(REPO_ROOT / "biospur_tag_positioning_offline_solver"))
    return load_module(STATIC_REPLAY, "audit_static_tag_raw_replay_matrix")


def load_current_layout() -> tuple[list[str], np.ndarray, np.ndarray, float, dict]:
    data = json.loads(CURRENT_LAYOUT.read_text(encoding="utf-8"))
    labels = [a.get("label", ANCHORS[int(a["id"])]) for a in data["anchors"]]
    coords = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in data["anchors"]], dtype=float)
    delays = np.array([float(a.get("d_anchor_mm") or 0.0) for a in data["anchors"]], dtype=float)
    return labels, coords, delays, float(data.get("tag_delay_mm") or 0.0), data


def load_anchor_truth() -> tuple[list[str], np.ndarray]:
    df = pd.read_csv(LAYOUT_ABS)
    df = df[(df["version"] == "v4-io") & (df["eval_set"] == "all8")].copy()
    df = df.sort_values("anchor")
    labels = df["anchor"].astype(str).tolist()
    truth = df[["truth_x_mm", "truth_y_vertical_mm", "truth_z_mm"]].to_numpy(dtype=float)
    return labels, truth


def load_t4_static_points() -> tuple[list[str], np.ndarray, np.ndarray]:
    static = pd.read_csv(CURRENT_T4_STATIC).sort_values("ID")
    truth_df = pd.read_csv(CURRENT_T4_SESSION).sort_values("ID")
    ids = static["ID"].astype(str).tolist()
    truth_ids = truth_df["ID"].astype(str).tolist()
    if ids != truth_ids:
        raise RuntimeError("static T4 rows and truth rows are not sorted to the same ID sequence")
    pts = static[["mean_x", "mean_y", "mean_z"]].to_numpy(dtype=float)
    truth = truth_df[["truth_x_mm", "truth_y_vertical_mm", "truth_z_mm"]].to_numpy(dtype=float)
    return ids, pts, truth


def fit_similarity(
    src: np.ndarray,
    dst: np.ndarray,
    *,
    allow_reflection: bool = True,
    allow_scale: bool = False,
) -> FitResult:
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    scale = 1.0
    if allow_scale:
        denom = float(np.sum(x * x))
        scale = float(np.sum(s * d) / denom) if denom > 0 else 1.0
    t = dst_c - scale * src_c @ r
    aligned = scale * src @ r + t
    return FitResult(aligned=aligned, rotation=r, translation=t, scale=scale, det=float(np.linalg.det(r)))


def apply_fit(points: np.ndarray, fit: FitResult) -> np.ndarray:
    return fit.scale * points @ fit.rotation + fit.translation


def error_summary(aligned: np.ndarray, truth: np.ndarray) -> dict[str, float]:
    err = np.linalg.norm(aligned - truth, axis=1)
    return {
        "median_mm": float(np.median(err)),
        "rmse_mm": float(np.sqrt(np.mean(err * err))),
        "p95_mm": float(np.percentile(err, 95)),
        "max_mm": float(np.max(err)),
    }


def pair_rows(src: np.ndarray, truth: np.ndarray) -> list[dict]:
    rows: list[dict] = []
    for i, a in enumerate(ANCHORS):
        for j in range(i + 1, len(ANCHORS)):
            b = ANCHORS[j]
            d_auto = float(np.linalg.norm(src[i] - src[j]))
            d_vicon = float(np.linalg.norm(truth[i] - truth[j]))
            rows.append(
                {
                    "pair": f"{a}-{b}",
                    "i": i,
                    "j": j,
                    "anchor_i": a,
                    "anchor_j": b,
                    "autopos_distance_mm": d_auto,
                    "vicon_distance_mm": d_vicon,
                    "delta_mm": d_auto - d_vicon,
                    "ratio_autopos_over_vicon": d_auto / d_vicon if d_vicon > 0 else math.nan,
                    "abs_scale_error_pct": abs(d_auto / d_vicon - 1.0) * 100.0 if d_vicon > 0 else math.nan,
                    "involves_C": a == "C" or b == "C",
                    "involves_D": a == "D" or b == "D",
                    "involves_C_or_D": a in {"C", "D"} or b in {"C", "D"},
                }
            )
    return rows


def ols_with_intercept(x: np.ndarray, y: np.ndarray) -> dict:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    design = np.column_stack([np.ones_like(x), x])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    pred = design @ beta
    resid = y - pred
    n, p = design.shape
    sse = float(np.sum(resid * resid))
    dof = n - p
    s2 = sse / dof
    cov = s2 * np.linalg.inv(design.T @ design)
    se = np.sqrt(np.diag(cov))
    tcrit = float(student_t.ppf(0.975, dof))
    sst = float(np.sum((y - y.mean()) ** 2))
    return {
        "n": int(n),
        "b0_mm": float(beta[0]),
        "b1_mm_per_m": float(beta[1]),
        "b0_se_mm": float(se[0]),
        "b1_se_mm_per_m": float(se[1]),
        "b1_ci95_low_mm_per_m": float(beta[1] - tcrit * se[1]),
        "b1_ci95_high_mm_per_m": float(beta[1] + tcrit * se[1]),
        "rmse_mm": float(np.sqrt(sse / n)),
        "sse": sse,
        "centered_r2": float(1.0 - sse / sst) if sst > 0 else math.nan,
        "note": "i.i.d. OLS CI; optimistic because the 28 pairs share per-anchor error terms",
    }


def additive_unconstrained(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, dict]:
    y = np.array([r["delta_mm"] for r in rows], dtype=float)
    design = np.zeros((len(rows), len(ANCHORS)), dtype=float)
    for k, r in enumerate(rows):
        design[k, int(r["i"])] = 1.0
        design[k, int(r["j"])] = 1.0
    delta, *_ = np.linalg.lstsq(design, y, rcond=None)
    pred = design @ delta
    resid = y - pred
    return delta, pred, model_stats(y, pred, n_params=len(ANCHORS))


def additive_with_intercept_zero_mean(rows: list[dict]) -> tuple[float, np.ndarray, np.ndarray, dict]:
    y = np.array([r["delta_mm"] for r in rows], dtype=float)
    design = np.zeros((len(rows), len(ANCHORS) + 1), dtype=float)
    design[:, 0] = 1.0
    for k, r in enumerate(rows):
        design[k, 1 + int(r["i"])] = 1.0
        design[k, 1 + int(r["j"])] = 1.0
    h = design.T @ design
    g = design.T @ y
    c = np.zeros((1, len(ANCHORS) + 1), dtype=float)
    c[0, 1:] = 1.0
    kkt = np.block([[h, c.T], [c, np.zeros((1, 1))]])
    rhs = np.r_[g, 0.0]
    sol = np.linalg.solve(kkt, rhs)[: len(ANCHORS) + 1]
    pred = design @ sol
    return float(sol[0]), sol[1:], pred, model_stats(y, pred, n_params=len(ANCHORS))


def additive_with_intercept_a_gauge(rows: list[dict]) -> tuple[float, np.ndarray, np.ndarray, dict]:
    y = np.array([r["delta_mm"] for r in rows], dtype=float)
    design = np.zeros((len(rows), len(ANCHORS)), dtype=float)
    design[:, 0] = 1.0
    for k, r in enumerate(rows):
        for idx in (int(r["i"]), int(r["j"])):
            if idx > 0:
                design[k, idx] += 1.0
    theta, *_ = np.linalg.lstsq(design, y, rcond=None)
    rel = np.zeros(len(ANCHORS), dtype=float)
    rel[1:] = theta[1:]
    pred = design @ theta
    return float(theta[0]), rel, pred, model_stats(y, pred, n_params=len(ANCHORS))


def model_stats(y: np.ndarray, pred: np.ndarray, n_params: int) -> dict:
    resid = y - pred
    sse = float(np.sum(resid * resid))
    sst_center = float(np.sum((y - y.mean()) ** 2))
    sst_zero = float(np.sum(y * y))
    n = len(y)
    return {
        "n": int(n),
        "n_params": int(n_params),
        "sse": sse,
        "rmse_mm": float(np.sqrt(sse / n)),
        "centered_r2": float(1.0 - sse / sst_center) if sst_center > 0 else math.nan,
        "signal_ss_explained_vs_zero": float(1.0 - sse / sst_zero) if sst_zero > 0 else math.nan,
    }


def model_comparison(rows: list[dict], ols: dict, additive_stats: dict) -> list[dict]:
    y = np.array([r["delta_mm"] for r in rows], dtype=float)
    x_m = np.array([r["vicon_distance_mm"] / 1000.0 for r in rows], dtype=float)
    const_pred = np.full_like(y, y.mean())
    prop_pred = ols["b0_mm"] + ols["b1_mm_per_m"] * x_m
    out = [
        {"model": "constant_offset", **model_stats(y, const_pred, 1), "description": "Delta=b0"},
        {
            "model": "distance_OLS_with_intercept",
            **model_stats(y, prop_pred, 2),
            "description": "Delta=b0+b1*d_ij",
        },
        {
            "model": "per_anchor_additive",
            **additive_stats,
            "description": "Delta=delta_i+delta_j, unconstrained endpoint deltas",
        },
    ]
    return out


def plot_ols(rows: list[dict], ols: dict) -> None:
    x = np.array([r["vicon_distance_mm"] / 1000.0 for r in rows], dtype=float)
    y = np.array([r["delta_mm"] for r in rows], dtype=float)
    fig, ax = plt.subplots(figsize=(7.0, 4.4), dpi=180)
    ax.scatter(x, y, s=38, color="#0072B2", edgecolor="white", linewidth=0.6)
    xx = np.linspace(x.min() * 0.95, x.max() * 1.03, 200)
    ax.plot(xx, ols["b0_mm"] + ols["b1_mm_per_m"] * xx, color="#D55E00", linewidth=1.8)
    ax.axhline(y.mean(), color="#555555", linestyle="--", linewidth=1.2, label=f"mean {y.mean():.1f} mm")
    ax.axhline(0.0, color="0.35", linewidth=0.8)
    for row in rows:
        ax.annotate(
            row["pair"],
            (row["vicon_distance_mm"] / 1000.0, row["delta_mm"]),
            xytext=(2, 2),
            textcoords="offset points",
            fontsize=6,
        )
    ax.set_xlabel("Vicon inter-anchor distance [m]")
    ax.set_ylabel("AutoPos - Vicon distance [mm]")
    ax.set_title("Revised OLS: pairwise range excess vs baseline")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGS / "item1_revised_ols_delta_vs_distance.png")
    plt.close(fig)


def save_layout_json(path: Path, version: str, label: str, x: np.ndarray, dly: np.ndarray, extra: dict) -> None:
    obj = {
        "version": version,
        "label": label,
        "anchor_ids": list(range(8)),
        "anchors": [
            {
                "id": int(i),
                "label": ANCHORS[i],
                "x_mm": float(x[i, 0]),
                "y_mm": float(x[i, 1]),
                "z_mm": float(x[i, 2]),
                "d_anchor_mm": float(dly[i]),
            }
            for i in range(8)
        ],
        "tag_delay_mm": 0.0,
        "stats": {},
        "extra": extra,
    }
    dump_json(path, obj)


def solve_v4_bound(mod, pair_dists: dict[tuple[int, int], float], anchor_ids: list[int], x_init: np.ndarray, bound: float):
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    pmap = mod.pos_param_map(n)

    def unpack(v):
        x = mod.unpack_pos(v[: len(pmap)], n)
        d = np.zeros(n)
        if n > 1:
            d[1:] = v[len(pmap) :]
        return x, d

    def fun(v):
        x, dly = unpack(v)
        out = [
            (np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist) / 15.0
            for (i, j), dist in lp.items()
        ]
        if n > 1:
            out.extend((dly[1:] / 20.0).tolist())
        out.extend(mod.physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out)

    x0 = np.r_[mod.pack_pos(x_init), np.zeros(max(0, n - 1))]
    lo = np.r_[np.full(len(pmap), -np.inf), np.full(max(0, n - 1), -float(bound))]
    hi = np.r_[np.full(len(pmap), np.inf), np.full(max(0, n - 1), float(bound))]
    res = least_squares(fun, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=5000)
    x, dly = unpack(res.x)
    extra = {
        "success": bool(res.success),
        "delay_bound_mm": float(bound),
        "physical_diagnostics": mod.layout_physical_diagnostics(x, anchor_ids),
        "nfev": int(res.nfev),
        "cost": float(res.cost),
    }
    return mod.gauge_align_local(x), dly, extra


def run_relaxed_bound_layouts(src_current: np.ndarray, truth: np.ndarray, baseline_static: dict) -> list[dict]:
    fc = load_module(RUN_CLEAN, "audit_run_clean_full_compare")
    fc.DATA = STAGED
    fc.SWEEP_CSV = STAGED / "sweep1000/pairs_all.csv"
    mod = fc.load_eval_module()
    anchor_ids = list(range(8))
    raw = fc.load_sweep_grouped()
    raw_solve = fc.slice_raw(raw, "all")
    mod.ANCHOR_SIGMA = fc.compute_anchor_sigma(mod, raw_solve)
    fused = fc.fuse_all(mod, raw_solve, anchor_ids)
    init, _ = mod.solve_autopos_v1(fused["v3"], anchor_ids)

    sigma_obj = {ANCHORS[i]: float(mod.ANCHOR_SIGMA[i]) for i in range(8)}
    sigma_path = LAYOUTS / "tables/anchor_sigma.json"
    dump_json(sigma_path, sigma_obj)

    static_mod = load_static_replay_module()
    out_rows: list[dict] = []
    for bound in (150.0, 200.0):
        version = f"v4io_bound{int(bound)}"
        print(f"[audit] solving relaxed delay bound +/-{bound:.0f} mm", flush=True)
        x, dly, extra = solve_v4_bound(mod, fused["v3"], anchor_ids, init, bound)
        layout_path = LAYOUTS / version / "layout.json"
        save_layout_json(layout_path, version, f"V4-io delay bound {bound:.0f} mm", x, dly, extra)

        rigid = fit_similarity(x, truth, allow_reflection=True, allow_scale=False)
        sim = fit_similarity(x, truth, allow_reflection=True, allow_scale=True)
        rigid_stats = error_summary(rigid.aligned, truth)
        static_summary, static_rows = evaluate_static_t4_mean(
            static_mod=static_mod,
            layout_path=layout_path,
            sigma_path=sigma_path,
            version=version,
        )
        write_csv(TABLES / f"item3_static_t4_mean_sessions_{version}.csv", static_rows)
        out_rows.append(
            {
                "case": version,
                "delay_bound_mm": bound,
                "sim3_scale_autopos_to_vicon": sim.scale,
                "current_sim3_scale_autopos_to_vicon": baseline_static["current_sim3_scale_autopos_to_vicon"],
                "rigid_anchor_median_mm": rigid_stats["median_mm"],
                "rigid_anchor_rmse_mm": rigid_stats["rmse_mm"],
                "current_rigid_anchor_median_mm": baseline_static["current_rigid_anchor_median_mm"],
                "current_rigid_anchor_rmse_mm": baseline_static["current_rigid_anchor_rmse_mm"],
                "static_t4_mean_n": static_summary["n_sessions"],
                "static_t4_mean_median_mm": static_summary["err_3d_median_mm"],
                "static_t4_mean_p95_mm": static_summary["err_3d_p95_mm"],
                "static_t4_mean_rmse_mm": static_summary["err_3d_rms_mm"],
                "current_static_t4_mean_median_mm": baseline_static["current_static_t4_mean_median_mm"],
                "current_static_t4_mean_p95_mm": baseline_static["current_static_t4_mean_p95_mm"],
                "current_static_t4_mean_rmse_mm": baseline_static["current_static_t4_mean_rmse_mm"],
                "delay_A_mm": float(dly[0]),
                "delay_B_mm": float(dly[1]),
                "delay_C_mm": float(dly[2]),
                "delay_D_mm": float(dly[3]),
                "delay_E_mm": float(dly[4]),
                "delay_F_mm": float(dly[5]),
                "delay_G_mm": float(dly[6]),
                "delay_H_mm": float(dly[7]),
                "n_saturated_within_1mm": int(np.sum(np.abs(np.abs(dly) - bound) <= 1.0)),
                "saturated_anchors_within_1mm": ",".join(
                    ANCHORS[i] for i, d in enumerate(dly) if abs(abs(d) - bound) <= 1.0
                ),
                "layout_json": str(layout_path),
                "static_session_table": str(TABLES / f"item3_static_t4_mean_sessions_{version}.csv"),
            }
        )
    return out_rows


def evaluate_static_t4_mean(static_mod, layout_path: Path, sigma_path: Path, version: str) -> tuple[dict, list[dict]]:
    layout = static_mod.load_layout_json(layout_path, sigma_path)
    labels, coords = static_mod.load_autopos_layout_coords(layout_path)
    metadata = static_mod.load_static_metadata(CURRENT_T4_STATIC)
    anchor_truth, tag_truth, tag_truth_meta, _correction_rows = static_mod.load_truth(
        OFFICIAL_ROOT / "opti_captures/full"
    )
    idx = [labels.index(a) for a in ANCHORS]
    src = coords[idx]
    dst = np.array([anchor_truth[a] for a in ANCHORS], dtype=float)
    rigid = static_mod.fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
    _sr, _st, scale_diag, _sdet = static_mod.fit_similarity(src, dst, allow_reflection=True, allow_scale=True)
    anchor_centroid = dst.mean(axis=0)

    static_files = sorted((OFFICIAL_ROOT / "captures/erlangen_20260528_optitrack").glob("static_ID*/tag_capture*/tr_all.csv"))
    rows: list[dict] = []
    for path in static_files:
        sid = static_mod.session_id_from_path(path)
        frames = static_mod.read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
        frames = sorted(frames, key=lambda f: (float(f.host_elapsed_s), int(f.sweep)))
        frames = static_mod.filter_frames(frames, set(range(8)), min_anchors=4)
        results = static_mod.solve_frames(layout, "T4", frames)
        summary = static_mod.summarize_results(results, "mean")
        truth = tag_truth.get(sid)
        if truth is None or summary["status"] != "ok":
            continue
        point = np.array([[summary["x_mm"], summary["y_mm"], summary["z_mm"]]], dtype=float)
        aligned = static_mod.apply_transform(point, rigid[0], rigid[1], rigid[2])[0]
        diff = aligned - truth
        meta = metadata.get(sid, {})
        truth_info = tag_truth_meta.get(sid, {})
        rows.append(
            {
                "version": version,
                "tag_method": "T4",
                "point_estimator": "mean",
                "eval_set": "all8",
                "ID": sid,
                "capture": static_mod.capture_name_from_path(path),
                "location": meta.get("location", ""),
                "height": meta.get("height", ""),
                "facing": meta.get("facing", ""),
                "tag_truth_source": truth_info.get("tag_truth_source", ""),
                "tag_truth_corrected": truth_info.get("tag_truth_corrected", False),
                "frames_input": int(len(frames)),
                "frames_solved": int(summary["frames_solved"]),
                **summary,
                "aligned_x_mm": float(aligned[0]),
                "aligned_y_vertical_mm": float(aligned[1]),
                "aligned_z_mm": float(aligned[2]),
                "truth_x_mm": float(truth[0]),
                "truth_y_vertical_mm": float(truth[1]),
                "truth_z_mm": float(truth[2]),
                "err_x_mm": float(diff[0]),
                "err_y_vertical_mm": float(diff[1]),
                "err_z_mm": float(diff[2]),
                "err_horizontal_mm": float(math.sqrt(diff[0] * diff[0] + diff[2] * diff[2])),
                "err_vertical_mm": float(abs(diff[1])),
                "err_3d_mm": float(np.linalg.norm(diff)),
                "anchor_fit_det": float(rigid[3]),
                "anchor_fit_scale": float(rigid[2]),
                "anchor_similarity_scale_diagnostic": float(scale_diag),
                "distance_to_array_centroid_mm": float(np.linalg.norm(truth - anchor_centroid)),
                "scale_bias_expected_mm": float(abs(1.0 - scale_diag) * np.linalg.norm(truth - anchor_centroid)),
                "source_tr_all": str(path),
                "layout_json": str(layout_path),
            }
        )
    df = pd.DataFrame(rows)
    err = df["err_3d_mm"].to_numpy(dtype=float)
    summary = {
        "version": version,
        "tag_method": "T4",
        "point_estimator": "mean",
        "eval_set": "all8",
        "n_sessions": int(len(df)),
        "err_3d_median_mm": float(np.nanmedian(err)),
        "err_3d_p95_mm": float(np.nanpercentile(err, 95)),
        "err_3d_rms_mm": float(np.sqrt(np.nanmean(err * err))),
        "err_horizontal_median_mm": float(np.nanmedian(df["err_horizontal_mm"].to_numpy(dtype=float))),
        "err_vertical_median_mm": float(np.nanmedian(df["err_vertical_mm"].to_numpy(dtype=float))),
    }
    return summary, rows


def metrics_for_truth(src: np.ndarray, truth: np.ndarray, static_points: np.ndarray, static_truth: np.ndarray) -> dict:
    pairs = pair_rows(src, truth)
    x_m = np.array([r["vicon_distance_mm"] / 1000.0 for r in pairs], dtype=float)
    y = np.array([r["delta_mm"] for r in pairs], dtype=float)
    ols = ols_with_intercept(x_m, y)
    delta, _pred, _stats = additive_unconstrained(pairs)
    rigid = fit_similarity(src, truth, allow_reflection=True, allow_scale=False)
    sim = fit_similarity(src, truth, allow_reflection=True, allow_scale=True)
    rigid_stats = error_summary(rigid.aligned, truth)
    aligned_static = apply_fit(static_points, rigid)
    static_err = np.linalg.norm(aligned_static - static_truth, axis=1)
    out = {
        "rigid_anchor_median_mm": rigid_stats["median_mm"],
        "rigid_anchor_rmse_mm": rigid_stats["rmse_mm"],
        "sim3_scale_autopos_to_vicon": sim.scale,
        "mean_delta_mm": float(np.mean(y)),
        "ols_b0_mm": ols["b0_mm"],
        "ols_b1_mm_per_m": ols["b1_mm_per_m"],
        "static_t4_mean_median_mm": float(np.median(static_err)),
    }
    for i, a in enumerate(ANCHORS):
        out[f"delta_{a}_mm"] = float(delta[i])
    return out


def summarize_metric_distribution(name: str, rows: list[dict], metric_names: list[str]) -> list[dict]:
    out: list[dict] = []
    if not rows:
        return out
    df = pd.DataFrame(rows)
    for metric in metric_names:
        vals = df[metric].to_numpy(dtype=float)
        out.append(
            {
                "model": name,
                "metric": metric,
                "n": int(len(vals)),
                "p50": float(np.nanpercentile(vals, 50)),
                "p5": float(np.nanpercentile(vals, 5)),
                "p95": float(np.nanpercentile(vals, 95)),
                "min": float(np.nanmin(vals)),
                "max": float(np.nanmax(vals)),
            }
        )
    return out


def registration_sensitivity(src: np.ndarray, truth: np.ndarray, baseline_metrics: dict) -> tuple[list[dict], list[dict]]:
    ids, static_points, static_truth = load_t4_static_points()
    rng = np.random.default_rng(RNG_SEED)
    metric_names = [
        "rigid_anchor_median_mm",
        "rigid_anchor_rmse_mm",
        "sim3_scale_autopos_to_vicon",
        "mean_delta_mm",
        "ols_b0_mm",
        "ols_b1_mm_per_m",
        "delta_C_mm",
        "delta_D_mm",
        "static_t4_mean_median_mm",
    ]
    trial_rows: list[dict] = []
    summary_rows: list[dict] = []

    baseline = metrics_for_truth(src, truth, static_points, static_truth)
    baseline["model"] = "baseline_unperturbed"
    baseline["trial"] = 0
    trial_rows.append(baseline)
    summary_rows.extend(summarize_metric_distribution("baseline_unperturbed", [baseline], metric_names))

    m1_rows = []
    for trial in range(N_MC):
        pert = truth + rng.normal(0.0, 5.0, size=truth.shape)
        row = metrics_for_truth(src, pert, static_points, static_truth)
        row.update({"model": "M1_isotropic_gaussian_sigma5mm", "trial": trial})
        m1_rows.append(row)
    trial_rows.extend(m1_rows)
    summary_rows.extend(summarize_metric_distribution("M1_isotropic_gaussian_sigma5mm", m1_rows, metric_names))

    centroid = truth[:, [0, 2]].mean(axis=0)
    radial = truth[:, [0, 2]] - centroid[None, :]
    radial_norm = np.linalg.norm(radial, axis=1)
    radial_dir = np.zeros_like(radial)
    radial_dir[radial_norm > 1e-9] = radial[radial_norm > 1e-9] / radial_norm[radial_norm > 1e-9, None]

    for sign, model in [(1.0, "M2_radial_common_plus5mm_outward"), (-1.0, "M2_radial_common_minus5mm_inward")]:
        pert = truth.copy()
        pert[:, 0] += sign * 5.0 * radial_dir[:, 0]
        pert[:, 2] += sign * 5.0 * radial_dir[:, 1]
        row = metrics_for_truth(src, pert, static_points, static_truth)
        row.update({"model": model, "trial": 0})
        trial_rows.append(row)
        summary_rows.extend(summarize_metric_distribution(model, [row], metric_names))

    m2_rows = []
    for trial in range(N_MC):
        mag = float(rng.uniform(0.0, 5.0))
        pert = truth.copy()
        pert[:, 0] += mag * radial_dir[:, 0]
        pert[:, 2] += mag * radial_dir[:, 1]
        row = metrics_for_truth(src, pert, static_points, static_truth)
        row.update({"model": "M2_radial_common_U0_5mm_outward", "trial": trial, "radial_magnitude_mm": mag})
        m2_rows.append(row)
    trial_rows.extend(m2_rows)
    summary_rows.extend(summarize_metric_distribution("M2_radial_common_U0_5mm_outward", m2_rows, metric_names))

    for sign, model in [(1.0, "M3_vertical_common_plus5mm"), (-1.0, "M3_vertical_common_minus5mm")]:
        pert = truth.copy()
        # Vicon Y is the vertical axis in the official tables.
        pert[:, 1] += sign * 5.0
        row = metrics_for_truth(src, pert, static_points, static_truth)
        row.update({"model": model, "trial": 0})
        trial_rows.append(row)
        summary_rows.extend(summarize_metric_distribution(model, [row], metric_names))

    for row in trial_rows:
        for metric in metric_names:
            row[f"{metric}_shift_from_baseline"] = float(row[metric] - baseline[metric])

    return summary_rows, trial_rows


def main() -> int:
    started = time.perf_counter()
    TABLES.mkdir(parents=True, exist_ok=True)
    FIGS.mkdir(parents=True, exist_ok=True)
    LAYOUTS.mkdir(parents=True, exist_ok=True)

    labels, src, delays, tag_delay, layout_obj = load_current_layout()
    truth_labels, truth = load_anchor_truth()
    if labels != ANCHORS or truth_labels != ANCHORS:
        raise RuntimeError(f"unexpected anchor labels: layout={labels}, truth={truth_labels}")

    pairs = pair_rows(src, truth)
    write_csv(TABLES / "item1_pairwise_delta.csv", pairs)
    x_m = np.array([r["vicon_distance_mm"] / 1000.0 for r in pairs], dtype=float)
    y = np.array([r["delta_mm"] for r in pairs], dtype=float)
    ols = ols_with_intercept(x_m, y)
    ols["mean_delta_mm"] = float(np.mean(y))
    ols["median_delta_mm"] = float(np.median(y))
    dump_json(TABLES / "item1_ols_with_intercept.json", ols)
    write_csv(TABLES / "item1_ols_with_intercept.csv", [ols])
    plot_ols(pairs, ols)

    delta, add_pred, add_stats = additive_unconstrained(pairs)
    intercept_zm, delta_zm, _pred_zm, stats_zm = additive_with_intercept_zero_mean(pairs)
    intercept_a, delta_a, _pred_a, stats_a = additive_with_intercept_a_gauge(pairs)
    delta_rows = []
    for i, anchor in enumerate(ANCHORS):
        delta_rows.append(
            {
                "anchor": anchor,
                "unconstrained_endpoint_delta_mm": float(delta[i]),
                "zero_mean_gauge_intercept_pair_mm": float(intercept_zm),
                "zero_mean_gauge_delta_mm": float(delta_zm[i]),
                "A_gauge_intercept_pair_mm": float(intercept_a),
                "A_gauge_relative_delta_mm": float(delta_a[i]),
                "current_v4io_solver_delay_mm": float(delays[i]),
                "current_v4io_delay_at_plus60_bound": bool(abs(delays[i] - 60.0) <= 1.0),
            }
        )
    write_csv(TABLES / "item2_per_anchor_additive_deltas.csv", delta_rows)

    model_rows = model_comparison(pairs, ols, add_stats)
    model_rows.append({"model": "per_anchor_additive_zero_mean_gauge", **stats_zm, "description": "Delta=b0+eta_i+eta_j, sum eta=0"})
    model_rows.append({"model": "per_anchor_additive_A_gauge", **stats_a, "description": "Delta=b0+eta_i+eta_j, eta_A=0"})
    write_csv(TABLES / "item2_model_comparison.csv", model_rows)

    top8 = sorted(pairs, key=lambda r: r["abs_scale_error_pct"], reverse=True)[:8]
    for rank, row in enumerate(top8, start=1):
        row["rank_abs_scale_error"] = rank
    write_csv(TABLES / "item2_top8_fig4_pairwise_scale_errors.csv", top8)

    rigid_current = fit_similarity(src, truth, allow_reflection=True, allow_scale=False)
    sim_current = fit_similarity(src, truth, allow_reflection=True, allow_scale=True)
    rigid_stats = error_summary(rigid_current.aligned, truth)
    t4_acc = pd.read_csv(CURRENT_T4_ACCURACY).iloc[0].to_dict()
    baseline_static = {
        "current_sim3_scale_autopos_to_vicon": sim_current.scale,
        "current_rigid_anchor_median_mm": rigid_stats["median_mm"],
        "current_rigid_anchor_rmse_mm": rigid_stats["rmse_mm"],
        "current_static_t4_mean_median_mm": float(t4_acc["err_3d_median_mm"]),
        "current_static_t4_mean_p95_mm": float(t4_acc["err_3d_p95_mm"]),
        "current_static_t4_mean_rmse_mm": float(t4_acc["err_3d_rms_mm"]),
    }
    current_delay_rows = [
        {
            "anchor": ANCHORS[i],
            "d_anchor_mm": float(delays[i]),
            "within_1mm_of_plus60_bound": bool(abs(delays[i] - 60.0) <= 1.0),
        }
        for i in range(8)
    ]
    write_csv(TABLES / "item3_current_v4io_anchor_delays.csv", current_delay_rows)
    item3_rows = run_relaxed_bound_layouts(src, truth, baseline_static)
    write_csv(TABLES / "item3_relaxed_delay_bound_summary.csv", item3_rows)

    reg_summary, reg_trials = registration_sensitivity(src, truth, baseline_static)
    write_csv(TABLES / "item4_registration_sensitivity_summary.csv", reg_summary)
    write_csv(TABLES / "item4_registration_sensitivity_trials.csv", reg_trials)

    decision = "persists"
    for row in item3_rows:
        if abs(row["sim3_scale_autopos_to_vicon"] - 1.0) < 0.02:
            decision = "collapses_toward_1"
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "script": str(THIS),
        "elapsed_s": time.perf_counter() - started,
        "paths": {
            "tables_dir": str(TABLES),
            "figs_dir": str(FIGS),
            "layouts_dir": str(LAYOUTS),
            "current_layout": str(CURRENT_LAYOUT),
            "current_t4_static_csv": str(CURRENT_T4_STATIC),
        },
        "item1": ols,
        "item2": {
            "unconstrained_deltas_mm": {ANCHORS[i]: float(delta[i]) for i in range(8)},
            "zero_mean_intercept_pair_mm": float(intercept_zm),
            "zero_mean_deltas_mm": {ANCHORS[i]: float(delta_zm[i]) for i in range(8)},
            "A_gauge_intercept_pair_mm": float(intercept_a),
            "A_gauge_relative_deltas_mm": {ANCHORS[i]: float(delta_a[i]) for i in range(8)},
            "model_comparison": model_rows,
            "top8_pairs": top8,
        },
        "item3": {
            "baseline": baseline_static,
            "relaxed_bound_rows": item3_rows,
            "decision_rule_verdict": decision,
        },
        "item4": {
            "n_mc": N_MC,
            "seed": RNG_SEED,
            "summary_rows": reg_summary,
        },
    }
    dump_json(TABLES / "audit_phase1_revised_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
