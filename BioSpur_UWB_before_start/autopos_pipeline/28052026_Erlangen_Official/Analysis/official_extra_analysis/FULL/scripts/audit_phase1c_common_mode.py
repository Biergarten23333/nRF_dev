#!/usr/bin/env python3
"""Phase 1c: common-mode delay hypothesis tests.

This script appends no paper text.  It writes diagnostic tables/layouts under
official_extra_analysis/FULL/audit_phase1c for review.
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

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.stats import f as f_dist


ANCHORS = list("ABCDEFGH")
DTU_MM_ONE_WAY = 4.69
RNG_SEED = 20260611

THIS = Path(__file__).resolve()
FULL_ROOT = THIS.parents[1]
ANALYSIS_ROOT = THIS.parents[3]
OFFICIAL_ROOT = THIS.parents[4]
REPO_ROOT = OFFICIAL_ROOT.parents[1]

OUT = FULL_ROOT / "audit_phase1c"
TABLES = OUT / "tables"
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
STAGED = OFFICIAL_ROOT / "solver/work/field_dataset_staged"
CURRENT_T4_STATIC = (
    OFFICIAL_ROOT
    / "solver/work/field_dataset_staged/FULL-COMPARE-1000-production-T4-real/tables/static_all_captures.csv"
)
CURRENT_T4_ACCURACY = (
    ANALYSIS_ROOT
    / "official_extra_analysis/FULL_4way_comparison/production_method_probe/production_static_method_real_run_eval/tables/tag_accuracy_summary.csv"
)
PHASE1_SUMMARY = FULL_ROOT / "audit_phase1/tables/audit_phase1_revised_summary.json"
PHASE1_ITEM3 = FULL_ROOT / "audit_phase1/tables/item3_relaxed_delay_bound_summary.csv"


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
    return load_module(STATIC_REPLAY, "audit_phase1c_static_tag_raw_replay_matrix")


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


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_reflection: bool, allow_scale: bool) -> FitResult:
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
    return FitResult(aligned=scale * src @ r + t, rotation=r, translation=t, scale=scale, det=float(np.linalg.det(r)))


def error_summary(aligned: np.ndarray, truth: np.ndarray) -> dict[str, float]:
    err = np.linalg.norm(aligned - truth, axis=1)
    return {
        "median_mm": float(np.median(err)),
        "rmse_mm": float(np.sqrt(np.mean(err * err))),
        "p95_mm": float(np.percentile(err, 95)),
    }


def pair_rows_from_points(points: np.ndarray, truth: np.ndarray) -> list[dict]:
    rows: list[dict] = []
    for i, a in enumerate(ANCHORS):
        for j in range(i + 1, len(ANCHORS)):
            b = ANCHORS[j]
            d_auto = float(np.linalg.norm(points[i] - points[j]))
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
                }
            )
    return rows


def additive_fit(pair_rows: list[dict], value_key: str) -> tuple[np.ndarray, np.ndarray, dict]:
    y = np.array([float(r[value_key]) for r in pair_rows], dtype=float)
    design = np.zeros((len(pair_rows), len(ANCHORS)), dtype=float)
    for row_idx, row in enumerate(pair_rows):
        design[row_idx, int(row["i"])] = 1.0
        design[row_idx, int(row["j"])] = 1.0
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    pred = design @ beta
    resid = y - pred
    return beta, pred, {
        "n": int(len(y)),
        "n_params": int(len(ANCHORS)),
        "sse": float(np.sum(resid * resid)),
        "rmse_mm": float(np.sqrt(np.mean(resid * resid))),
        "residual_mean_mm": float(np.mean(resid)),
        "residual_p95_abs_mm": float(np.percentile(np.abs(resid), 95)),
    }


def f_test(sse_reduced: float, p_reduced: int, sse_full: float, p_full: int, n: int) -> dict:
    df_num = p_full - p_reduced
    df_den = n - p_full
    f_val = ((sse_reduced - sse_full) / df_num) / (sse_full / df_den)
    p_val = float(f_dist.sf(f_val, df_num, df_den))
    return {"F": float(f_val), "df_num": int(df_num), "df_den": int(df_den), "p": p_val}


def model_stats(y: np.ndarray, pred: np.ndarray, n_params: int) -> dict:
    resid = y - pred
    sse = float(np.sum(resid * resid))
    return {"sse": sse, "rmse_mm": float(np.sqrt(np.mean(resid * resid))), "n_params": int(n_params)}


def phase1_discrimination_stats() -> dict:
    s = json.loads(PHASE1_SUMMARY.read_text(encoding="utf-8"))
    item1 = s["item1"]
    item2 = s["item2"]
    rows = item2["model_comparison"]
    by_model = {r["model"]: r for r in rows}
    const = by_model["constant_offset"]
    ols = by_model["distance_OLS_with_intercept"]
    additive = by_model["per_anchor_additive"]
    n = int(additive["n"])
    current_scale = s["item3"]["baseline"]["current_sim3_scale_autopos_to_vicon"]
    pure_scale_slope = (1.0 / current_scale - 1.0) * 1000.0
    return {
        "ols_b1_mm_per_m": item1["b1_mm_per_m"],
        "ols_b1_ci95_low_mm_per_m": item1["b1_ci95_low_mm_per_m"],
        "ols_b1_ci95_high_mm_per_m": item1["b1_ci95_high_mm_per_m"],
        "pure_scale_slope_mm_per_m": pure_scale_slope,
        "pure_scale_slope_inside_ols_ci": bool(item1["b1_ci95_low_mm_per_m"] <= pure_scale_slope <= item1["b1_ci95_high_mm_per_m"]),
        "additive_vs_constant": f_test(const["sse"], const["n_params"], additive["sse"], additive["n_params"], n),
        "additive_vs_ols": f_test(ols["sse"], ols["n_params"], additive["sse"], additive["n_params"], n),
    }


def load_solver_inputs():
    fc = load_module(RUN_CLEAN, "audit_phase1c_run_clean_full_compare")
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
    return mod, anchor_ids, fused["v3"], init, sigma_path


def fused_pair_rows(fused_v3: dict[tuple[int, int], float], truth: np.ndarray) -> list[dict]:
    rows: list[dict] = []
    for i, a in enumerate(ANCHORS):
        for j in range(i + 1, len(ANCHORS)):
            b = ANCHORS[j]
            dhat = float(fused_v3[(i, j)])
            d_vicon = float(np.linalg.norm(truth[i] - truth[j]))
            rows.append(
                {
                    "pair": f"{a}-{b}",
                    "i": i,
                    "j": j,
                    "anchor_i": a,
                    "anchor_j": b,
                    "dhat_mm": dhat,
                    "vicon_distance_mm": d_vicon,
                    "oracle_excess_mm": dhat - d_vicon,
                }
            )
    return rows


def save_layout_json(
    path: Path,
    version: str,
    label: str,
    x: np.ndarray,
    d_anchor: np.ndarray,
    tag_delay_mm: float,
    extra: dict,
) -> None:
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
                "d_anchor_mm": float(d_anchor[i]),
            }
            for i in range(8)
        ],
        "tag_delay_mm": float(tag_delay_mm),
        "stats": {},
        "extra": extra,
    }
    dump_json(path, obj)


def solve_common_mode(
    mod,
    pair_dists: dict[tuple[int, int], float],
    anchor_ids: list[int],
    x_init: np.ndarray,
    *,
    c_init: float = 0.0,
    e_init: np.ndarray | None = None,
    c_fixed: float | None = None,
    position_noise_mm: np.ndarray | None = None,
    max_nfev: int = 5000,
) -> dict:
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    pmap = mod.pos_param_map(n)
    x_start = np.asarray(x_init, dtype=float).copy()
    if position_noise_mm is not None:
        x_start = mod.gauge_align_local(x_start + position_noise_mm)
    e0 = np.zeros(n, dtype=float) if e_init is None else np.asarray(e_init, dtype=float)
    e0 = np.clip(e0 - np.mean(e0), -80.0, 80.0)
    if c_fixed is None:
        x0 = np.r_[mod.pack_pos(x_start), float(c_init), e0]
        lo = np.r_[np.full(len(pmap), -np.inf), -150.0, np.full(n, -100.0)]
        hi = np.r_[np.full(len(pmap), np.inf), 150.0, np.full(n, 100.0)]
    else:
        x0 = np.r_[mod.pack_pos(x_start), e0]
        lo = np.r_[np.full(len(pmap), -np.inf), np.full(n, -100.0)]
        hi = np.r_[np.full(len(pmap), np.inf), np.full(n, 100.0)]

    def unpack(v):
        x = mod.unpack_pos(v[: len(pmap)], n)
        if c_fixed is None:
            c = float(v[len(pmap)])
            e = np.asarray(v[len(pmap) + 1 : len(pmap) + 1 + n], dtype=float)
        else:
            c = float(c_fixed)
            e = np.asarray(v[len(pmap) : len(pmap) + n], dtype=float)
        return x, c, e

    def residual(v):
        x, c, e = unpack(v)
        dly = c + e
        out = [
            (np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist) / 15.0
            for (i, j), dist in lp.items()
        ]
        out.extend((e / 20.0).tolist())
        # Strong zero-mean regularization makes c the common mode while keeping
        # least_squares/TRF bounds available for every e_i.
        out.append(float(np.mean(e) / 1.0))
        out.extend(mod.physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out, dtype=float)

    res = least_squares(residual, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=max_nfev)
    x, c, e = unpack(res.x)
    x = mod.gauge_align_local(x)
    dly = c + e
    raw_residual = residual(res.x)
    pair_resid = []
    for (i, j), dist in lp.items():
        pair_resid.append(float(np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist))
    return {
        "x": x,
        "c_mm": float(c),
        "e_mm": np.asarray(e, dtype=float),
        "d_anchor_mm": dly,
        "result": res,
        "cost": float(res.cost),
        "residual_l2": float(np.sum(raw_residual * raw_residual)),
        "pair_rmse_mm": float(np.sqrt(np.mean(np.asarray(pair_resid) ** 2))),
        "pair_residuals_mm": pair_resid,
        "mean_e_mm": float(np.mean(e)),
        "max_abs_e_mm": float(np.max(np.abs(e))),
        "success": bool(res.success),
    }


def evaluate_static_t4_mean(static_mod, layout_path: Path, sigma_path: Path, version: str) -> tuple[dict, list[dict]]:
    layout = static_mod.load_layout_json(layout_path, sigma_path)
    labels, coords = static_mod.load_autopos_layout_coords(layout_path)
    metadata = static_mod.load_static_metadata(CURRENT_T4_STATIC)
    anchor_truth, tag_truth, tag_truth_meta, _correction_rows = static_mod.load_truth(OFFICIAL_ROOT / "opti_captures/full")
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


def layout_metrics(x: np.ndarray, truth: np.ndarray) -> dict:
    rigid = fit_similarity(x, truth, allow_reflection=True, allow_scale=False)
    sim = fit_similarity(x, truth, allow_reflection=True, allow_scale=True)
    stats = error_summary(rigid.aligned, truth)
    return {
        "sim3_scale_autopos_to_vicon": sim.scale,
        "rigid_anchor_median_mm": stats["median_mm"],
        "rigid_anchor_rmse_mm": stats["rmse_mm"],
        "rigid_anchor_p95_mm": stats["p95_mm"],
    }


def main() -> int:
    started = time.perf_counter()
    TABLES.mkdir(parents=True, exist_ok=True)
    LAYOUTS.mkdir(parents=True, exist_ok=True)

    labels, current_xyz, current_delays, current_tag_delay, _layout_obj = load_current_layout()
    truth_labels, truth = load_anchor_truth()
    if labels != ANCHORS or truth_labels != ANCHORS:
        raise RuntimeError(f"unexpected labels layout={labels} truth={truth_labels}")

    current_metrics = layout_metrics(current_xyz, truth)
    current_static = pd.read_csv(CURRENT_T4_ACCURACY).iloc[0].to_dict()

    item0 = {
        "production_layout_json": str(CURRENT_LAYOUT),
        "production_tag_delay_mm": float(current_tag_delay),
        "phase1_relaxed_layouts_tag_delay_mm": 0.0,
        "tag_delay_parity_ok": bool(abs(current_tag_delay - 0.0) < 1e-9),
        "phase1_item3_static_numbers_superseded": bool(abs(current_tag_delay - 0.0) >= 1e-9),
        "note": "Production v4-io tag_delay_mm is 0, so Phase-1 relaxed static comparisons were not affected by the hardcoded 0.0 tag_delay.",
    }
    write_csv(TABLES / "item0_tag_delay_parity.csv", [item0])

    mod, anchor_ids, fused_v3, x_init, sigma_path = load_solver_inputs()
    fused_rows = fused_pair_rows(fused_v3, truth)
    oracle_d, oracle_pred, oracle_stats = additive_fit(fused_rows, "oracle_excess_mm")
    phase1_deltas = json.loads(PHASE1_SUMMARY.read_text(encoding="utf-8"))["item2"]["unconstrained_deltas_mm"]
    oracle_rows: list[dict] = []
    for i, anchor in enumerate(ANCHORS):
        oracle_rows.append(
            {
                "anchor": anchor,
                "oracle_d_i_mm": float(oracle_d[i]),
                "phase1_item2_delta_i_mm": float(phase1_deltas[anchor]),
                "difference_oracle_minus_phase1_delta_mm": float(oracle_d[i] - float(phase1_deltas[anchor])),
                "oracle_d_i_dtu_one_way_equiv": float(oracle_d[i] / DTU_MM_ONE_WAY),
            }
        )
    item1 = {
        **oracle_stats,
        "all_oracle_d_positive": bool(np.all(oracle_d > 0)),
        "mean_oracle_d_mm": float(np.mean(oracle_d)),
        "median_oracle_d_mm": float(np.median(oracle_d)),
        "sum_oracle_d_mm": float(np.sum(oracle_d)),
        "mean_oracle_d_dtu_one_way_equiv": float(np.mean(oracle_d) / DTU_MM_ONE_WAY),
        "dtu_convention": "1 DTU ~= 4.69 mm one-way range equivalent",
        "largest_anchor": ANCHORS[int(np.argmax(oracle_d))],
        "largest_oracle_d_mm": float(np.max(oracle_d)),
    }
    write_csv(TABLES / "item1_oracle_pair_excess.csv", fused_rows)
    write_csv(TABLES / "item1_oracle_per_anchor_delay.csv", oracle_rows)
    write_csv(TABLES / "item1_oracle_summary.csv", [item1])

    static_mod = load_static_replay_module()
    main = solve_common_mode(mod, fused_v3, anchor_ids, x_init, c_init=0.0)
    version = "v4io_common_mode"
    main_layout = LAYOUTS / version / "layout.json"
    save_layout_json(
        main_layout,
        version,
        "V4-io common-mode plus differential delay",
        main["x"],
        main["d_anchor_mm"],
        current_tag_delay,
        {
            "delay_model": "d_i = c + e_i; c unregularized; e_i regularized by 20 mm; mean(e) regularized by 1 mm",
            "success": main["success"],
            "cost": main["cost"],
            "pair_rmse_mm": main["pair_rmse_mm"],
            "mean_e_mm": main["mean_e_mm"],
            "max_abs_e_mm": main["max_abs_e_mm"],
        },
    )
    static_summary, static_rows = evaluate_static_t4_mean(static_mod, main_layout, sigma_path, version)
    write_csv(TABLES / "item2_static_t4_mean_sessions_common_mode.csv", static_rows)
    met = layout_metrics(main["x"], truth)
    item2 = {
        "case": version,
        "c_mm": main["c_mm"],
        "mean_e_mm": main["mean_e_mm"],
        "max_abs_e_mm": main["max_abs_e_mm"],
        "cost": main["cost"],
        "residual_l2": main["residual_l2"],
        "pair_rmse_mm": main["pair_rmse_mm"],
        **met,
        "current_sim3_scale_autopos_to_vicon": current_metrics["sim3_scale_autopos_to_vicon"],
        "current_rigid_anchor_median_mm": current_metrics["rigid_anchor_median_mm"],
        "current_rigid_anchor_rmse_mm": current_metrics["rigid_anchor_rmse_mm"],
        "static_t4_mean_median_mm": static_summary["err_3d_median_mm"],
        "static_t4_mean_p95_mm": static_summary["err_3d_p95_mm"],
        "static_t4_mean_rmse_mm": static_summary["err_3d_rms_mm"],
        "current_static_t4_mean_median_mm": float(current_static["err_3d_median_mm"]),
        "current_static_t4_mean_p95_mm": float(current_static["err_3d_p95_mm"]),
        "current_static_t4_mean_rmse_mm": float(current_static["err_3d_rms_mm"]),
        "layout_json": str(main_layout),
    }
    item2_rows = []
    for i, anchor in enumerate(ANCHORS):
        item2_rows.append(
            {
                "anchor": anchor,
                "c_mm": main["c_mm"],
                "e_i_mm": float(main["e_mm"][i]),
                "d_i_c_plus_e_i_mm": float(main["d_anchor_mm"][i]),
                "oracle_d_i_mm": float(oracle_d[i]),
                "phase1_item2_delta_i_mm": float(phase1_deltas[anchor]),
            }
        )
    write_csv(TABLES / "item2_common_mode_summary.csv", [item2])
    write_csv(TABLES / "item2_common_mode_anchor_delays.csv", item2_rows)

    clamped_rows = []
    for c_fixed in (0.0, main["c_mm"]):
        sol = solve_common_mode(mod, fused_v3, anchor_ids, x_init, c_fixed=c_fixed)
        version_c = f"v4io_common_mode_cfixed_{c_fixed:.3f}".replace("-", "neg").replace(".", "p")
        layout_path = LAYOUTS / version_c / "layout.json"
        save_layout_json(
            layout_path,
            version_c,
            f"V4-io common-mode solve with c fixed {c_fixed:.3f} mm",
            sol["x"],
            sol["d_anchor_mm"],
            current_tag_delay,
            {"delay_model": "c fixed; e_i bounded +/-100 and regularized", "success": sol["success"], "cost": sol["cost"]},
        )
        row = {
            "case": version_c,
            "c_fixed_mm": c_fixed,
            "cost": sol["cost"],
            "cost_delta_vs_free": float(sol["cost"] - main["cost"]),
            "residual_l2": sol["residual_l2"],
            "pair_rmse_mm": sol["pair_rmse_mm"],
            "mean_e_mm": sol["mean_e_mm"],
            "max_abs_e_mm": sol["max_abs_e_mm"],
            **layout_metrics(sol["x"], truth),
            "layout_json": str(layout_path),
        }
        clamped_rows.append(row)
    write_csv(TABLES / "item3_c_clamp_comparison.csv", clamped_rows)

    rng = np.random.default_rng(RNG_SEED)
    init_rows = []
    for trial in range(5):
        pos_noise = rng.normal(0.0, 50.0, size=x_init.shape)
        c0 = float(rng.uniform(-40.0, 100.0))
        e0 = rng.normal(0.0, 25.0, size=8)
        sol = solve_common_mode(mod, fused_v3, anchor_ids, x_init, c_init=c0, e_init=e0, position_noise_mm=pos_noise)
        row = {
            "trial": trial,
            "c_init_mm": c0,
            "c_fit_mm": sol["c_mm"],
            "cost": sol["cost"],
            "cost_delta_vs_free": float(sol["cost"] - main["cost"]),
            "pair_rmse_mm": sol["pair_rmse_mm"],
            "mean_e_mm": sol["mean_e_mm"],
            "max_abs_e_mm": sol["max_abs_e_mm"],
            "success": sol["success"],
            **layout_metrics(sol["x"], truth),
        }
        init_rows.append(row)
    write_csv(TABLES / "item3_perturbed_init_spread.csv", init_rows)

    c_vals = np.array([r["c_fit_mm"] for r in init_rows], dtype=float)
    scale_vals = np.array([r["sim3_scale_autopos_to_vicon"] for r in init_rows], dtype=float)
    item3 = {
        "free_cost": main["cost"],
        "free_c_mm": main["c_mm"],
        "clamped_rows": clamped_rows,
        "perturbed_init_n": 5,
        "perturbed_c_min_mm": float(np.min(c_vals)),
        "perturbed_c_max_mm": float(np.max(c_vals)),
        "perturbed_c_std_mm": float(np.std(c_vals, ddof=1)),
        "perturbed_scale_min": float(np.min(scale_vals)),
        "perturbed_scale_max": float(np.max(scale_vals)),
        "perturbed_scale_std": float(np.std(scale_vals, ddof=1)),
        "valley_note": "Compare c=0 and c=fitted costs, plus perturbed-init spread. Small differences would indicate weak c/scale identifiability.",
    }
    write_csv(TABLES / "item3_identifiability_summary.csv", [item3])

    discrim = phase1_discrimination_stats()
    scale_collapsed = abs(item2["sim3_scale_autopos_to_vicon"] - 1.0) <= 0.01
    c_near_oracle = abs(item2["c_mm"] - item1["mean_oracle_d_mm"]) <= 20.0
    c_spread = item3["perturbed_c_max_mm"] - item3["perturbed_c_min_mm"]
    scale_spread = item3["perturbed_scale_max"] - item3["perturbed_scale_min"]
    c0_row = next(r for r in clamped_rows if abs(r["c_fixed_mm"]) < 1e-9)
    valley_flat = (c0_row["cost_delta_vs_free"] < 10.0) or (c_spread > 25.0) or (scale_spread > 0.01)
    if scale_collapsed and c_near_oracle and not valley_flat:
        verdict = "a_common_mode_recoverable"
    elif valley_flat:
        verdict = "b_common_mode_scale_weakly_identified"
    else:
        verdict = "c_mixed"
    item4 = {
        "supported_verdict": verdict,
        "scale_collapsed_within_1pct": bool(scale_collapsed),
        "c_near_oracle_mean_within_20mm": bool(c_near_oracle),
        "valley_flat_flag": bool(valley_flat),
        "recommendation": "Use recommendations only; do not replace production layout from this diagnostic run.",
        **discrim,
    }
    write_csv(TABLES / "item4_verdict_synthesis.csv", [item4])

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_s": time.perf_counter() - started,
        "script": str(THIS),
        "paths": {
            "tables_dir": str(TABLES),
            "layouts_dir": str(LAYOUTS),
            "current_layout": str(CURRENT_LAYOUT),
            "summary_json": str(TABLES / "audit_phase1c_summary.json"),
        },
        "item0": item0,
        "item1_oracle": item1,
        "item2_common_mode": item2,
        "item3_identifiability": item3,
        "item4_verdict": item4,
    }
    dump_json(TABLES / "audit_phase1c_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
