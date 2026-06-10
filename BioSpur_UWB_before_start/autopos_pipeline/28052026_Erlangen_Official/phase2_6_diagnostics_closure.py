#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from phase2_solver_ablation import (
    alignment_metrics,
    fit_tag_bias_model,
    load_full_compare_module,
    load_primary_vicon_anchor_truth,
    load_sweep_deltas,
    save_layout_json,
    summarize_values,
    write_csv,
)
from scripts.audit_helpers import ANCHOR_LABELS, markdown_table
from scripts.phase1_common import anchor_coord_map, load_phase1_data, tag_coord_map


VARIANT_ORDER = ["baseline_v4io", "V-A_unbounded", "V-B_calibrated", "V-C_calibrated_residual"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2.6 diagnostics closure, no production changes.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def load_layout_json_plain(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    anchors = sorted(data["anchors"], key=lambda a: int(a["id"]))
    coords = np.asarray([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in anchors], dtype=float)
    delays = np.asarray([float(a.get("d_anchor_mm", 0.0)) for a in anchors], dtype=float)
    labels = [str(a.get("label") or ANCHOR_LABELS[int(a["id"])]) for a in anchors]
    return {"path": str(path), "data": data, "coords": coords, "delays": delays, "labels": labels}


def pairwise_scale_diagnostic(layouts: dict[str, dict], vicon_truth: dict[str, np.ndarray]) -> tuple[list[dict], list[dict]]:
    truth = np.asarray([vicon_truth[a] for a in ANCHOR_LABELS], dtype=float)
    rows: list[dict] = []
    for variant in VARIANT_ORDER:
        coords = layouts[variant]["coords"]
        for i in range(8):
            for j in range(i + 1, 8):
                auto = float(np.linalg.norm(coords[i] - coords[j]))
                vic = float(np.linalg.norm(truth[i] - truth[j]))
                err = (auto / vic - 1.0) * 100.0
                rows.append(
                    {
                        "variant": variant,
                        "pair": f"{ANCHOR_LABELS[i]}-{ANCHOR_LABELS[j]}",
                        "autopos_distance_mm": auto,
                        "vicon_distance_mm": vic,
                        "scale_error_percent": err,
                        "positive": bool(err > 0.0),
                    }
                )
    summary: list[dict] = []
    df = pd.DataFrame(rows)
    for variant in VARIANT_ORDER:
        g = df[df["variant"] == variant]
        vals = g["scale_error_percent"].to_numpy(dtype=float)
        summary.append(
            {
                "variant": variant,
                "pairs": int(len(g)),
                "median_scale_error_percent": float(np.median(vals)),
                "mean_scale_error_percent": float(np.mean(vals)),
                "rms_scale_error_percent": float(np.sqrt(np.mean(vals * vals))),
                "count_positive": int(np.sum(vals > 0.0)),
                "count_negative": int(np.sum(vals < 0.0)),
                "count_abs_lt_1_percent": int(np.sum(np.abs(vals) < 1.0)),
            }
        )
    return rows, summary


def plot_scale_hist(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    fig, axes = plt.subplots(2, 2, figsize=(9.5, 6.8), sharex=True, sharey=True)
    bins = np.linspace(-8.0, 14.0, 23)
    for ax, variant in zip(axes.ravel(), VARIANT_ORDER):
        vals = df[df["variant"] == variant]["scale_error_percent"].to_numpy(dtype=float)
        ax.hist(vals, bins=bins, color="#4878a8", edgecolor="white")
        ax.axvline(0.0, color="black", linewidth=1.0)
        ax.axvline(np.median(vals), color="#b54a3a", linestyle="--", linewidth=1.1)
        ax.set_title(variant)
        ax.grid(True, axis="y", alpha=0.25)
    for ax in axes[-1, :]:
        ax.set_xlabel("pairwise scale error vs Vicon [%]")
    for ax in axes[:, 0]:
        ax.set_ylabel("pair count")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def solve_v4_custom_signed(
    mod: Any,
    pair_dists: dict[tuple[int, int], float],
    anchor_ids: list[int],
    *,
    x_init: np.ndarray,
    delay_bound_mm: float,
    delay_prior_sigma_mm: float = 20.0,
    delay_sign: float = -1.0,
):
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    pmap = mod.pos_param_map(n)

    def unpack(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x = mod.unpack_pos(v[: len(pmap)], n)
        dly = np.zeros(n, dtype=float)
        dly[1:] = v[len(pmap) :]
        return x, dly

    def fun(v: np.ndarray) -> np.ndarray:
        x, dly = unpack(v)
        out = [
            (np.linalg.norm(x[i] - x[j]) + delay_sign * (dly[i] + dly[j]) - dist) / 15.0
            for (i, j), dist in lp.items()
        ]
        out.extend((dly[1:] / delay_prior_sigma_mm).tolist())
        out.extend(mod.physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out, dtype=float)

    x0 = np.r_[mod.pack_pos(x_init), np.zeros(max(0, n - 1))]
    lo = np.r_[np.full(len(pmap), -np.inf), np.full(max(0, n - 1), -abs(delay_bound_mm))]
    hi = np.r_[np.full(len(pmap), np.inf), np.full(max(0, n - 1), abs(delay_bound_mm))]
    res = least_squares(fun, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=7000)
    x, dly = unpack(res.x)
    res.physical_diagnostics = mod.layout_physical_diagnostics(x, anchor_ids)
    return mod.gauge_align_local(x), dly, res


def va_delay_sanity(
    data_dir: Path,
    out_dir: Path,
    layouts: dict[str, dict],
    vicon_truth: dict[str, np.ndarray],
) -> tuple[list[dict], dict, dict[str, dict]]:
    sweep_delta = load_sweep_deltas(out_dir)
    delta_a = float(sweep_delta["A"])
    va_delay = layouts["V-A_unbounded"]["delays"]
    rows: list[dict] = []
    for idx, anchor in enumerate(ANCHOR_LABELS):
        expected = 0.5 * (float(sweep_delta[anchor]) - delta_a)
        fitted = float(va_delay[idx])
        rows.append(
            {
                "anchor": anchor,
                "sweep_delta_full_mm": float(sweep_delta[anchor]),
                "expected_relative_delay_mm": expected,
                "va_fitted_delay_mm": fitted,
                "fitted_minus_expected_mm": fitted - expected,
                "same_sign_or_zero": bool(anchor == "A" or np.sign(expected) == np.sign(fitted)),
            }
        )

    non_a = [r for r in rows if r["anchor"] != "A"]
    sign_matches = int(sum(bool(r["same_sign_or_zero"]) for r in non_a))
    sign_disagrees = int(len(non_a) - sign_matches)
    summary = {
        "convention_in_solver_residual": "range residual uses distance + d_i + d_j - measured for V-A",
        "expected_relative_formula": "(Delta_i - Delta_A)/2",
        "non_A_sign_matches": sign_matches,
        "non_A_sign_disagrees": sign_disagrees,
        "rerun_triggered": bool(sign_disagrees > 0),
    }

    rerun_layouts: dict[str, dict] = {}
    if sign_disagrees > 0:
        fc = load_full_compare_module(data_dir)
        mod = fc.load_eval_module()
        anchor_ids = list(range(8))
        raw = fc.load_sweep_grouped()
        mod.ANCHOR_SIGMA = fc.compute_anchor_sigma(mod, raw)
        fused = fc.fuse_all(mod, raw, anchor_ids)["v3"]
        init_base, _ = mod.solve_autopos_v1(fused, anchor_ids)
        x_fix, d_fix, res_fix = solve_v4_custom_signed(
            mod,
            fused,
            anchor_ids,
            x_init=init_base,
            delay_bound_mm=400.0,
            delay_sign=-1.0,
        )
        fixed = fc.Layout(
            "V-A_subtractive_delay_sanity",
            "V-A subtractive-delay sign sanity",
            x_fix,
            d_fix,
            {"success": bool(res_fix.success), **getattr(res_fix, "physical_diagnostics", {})},
        )
        summary_fixed, _fit, _err_rows = alignment_metrics(fixed, vicon_truth)
        residuals = []
        lp, _g2l, _l2g = mod.local_pairs(fused, anchor_ids)
        for (i, j), dist in lp.items():
            residuals.append(float(np.linalg.norm(fixed.x[i] - fixed.x[j]) - fixed.dly[i] - fixed.dly[j] - dist))
        summary.update(
            {
                "sign_fixed_variant": fixed.version,
                "sign_fixed_anchor_median_3d_mm": summary_fixed["anchor_median_3d_mm"],
                "sign_fixed_anchor_rms_3d_mm": summary_fixed["anchor_rms_3d_mm"],
                "sign_fixed_shape_rms_mm": summary_fixed["shape_rms_mm"],
                "sign_fixed_pair_rms_mm": float(np.sqrt(np.mean(np.asarray(residuals, dtype=float) ** 2))),
                "sign_fixed_delay_min_mm": float(np.min(fixed.dly)),
                "sign_fixed_delay_max_mm": float(np.max(fixed.dly)),
            }
        )
        tmp = {
            "coords": np.asarray(fixed.x, dtype=float),
            "delays": np.asarray(fixed.dly, dtype=float),
            "labels": list(ANCHOR_LABELS),
        }
        rerun_layouts[fixed.version] = tmp
        save_layout_json(out_dir / "phase2_6_layouts" / fixed.version / "layout.json", fixed, anchor_ids)

        for idx, anchor in enumerate(ANCHOR_LABELS):
            expected = 0.5 * (float(sweep_delta[anchor]) - delta_a)
            rows[idx]["sign_fixed_delay_mm"] = float(fixed.dly[idx])
            rows[idx]["sign_fixed_minus_expected_mm"] = float(fixed.dly[idx] - expected)

    return rows, summary, rerun_layouts


def plot_va_delay(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(6.2, 5.4))
    x = df["expected_relative_delay_mm"].to_numpy(dtype=float)
    y = df["va_fitted_delay_mm"].to_numpy(dtype=float)
    ax.scatter(x, y, s=60, color="#4c78a8", label="V-A original")
    if "sign_fixed_delay_mm" in df.columns:
        y2 = pd.to_numeric(df["sign_fixed_delay_mm"], errors="coerce").to_numpy(dtype=float)
        ax.scatter(x, y2, s=58, marker="x", color="#b64b3b", label="subtractive sanity rerun")
    for _, row in df.iterrows():
        ax.annotate(str(row["anchor"]), (row["expected_relative_delay_mm"], row["va_fitted_delay_mm"]), xytext=(4, 4), textcoords="offset points")
    lo = float(min(np.nanmin(x), np.nanmin(y), -140.0) - 10)
    hi = float(max(np.nanmax(x), np.nanmax(y), 230.0) + 10)
    ax.plot([lo, hi], [lo, hi], color="black", linestyle="--", linewidth=1.0, label="1:1")
    ax.axhline(0, color="0.35", linewidth=0.8)
    ax.axvline(0, color="0.35", linewidth=0.8)
    ax.set_xlabel("expected relative delay from sweep Delta [(Delta_i-Delta_A)/2], mm")
    ax.set_ylabel("solver fitted d_i, mm")
    ax.set_title("V-A Delay Sign Sanity")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def safe_read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def tag_baseline_fidelity(data_dir: Path, out_dir: Path) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    candidate_rows: list[dict] = []

    phase2_summary = safe_read(out_dir / "tables" / "04_static_tag_transfer_summary.csv")
    if not phase2_summary.empty:
        g = phase2_summary[phase2_summary["variant"].astype(str) == "baseline_v4io"]
        if not g.empty:
            r = g.iloc[0]
            rows.append(
                {
                    "case": "phase2_simplified_WLS_baseline",
                    "median_3d_mm": float(r["static_tag_median_3d_mm"]),
                    "rms_3d_mm": float(r["static_tag_rmse_3d_mm"]),
                    "point_estimator": "one median link set per position",
                    "registration": "Phase 2 3D rigid anchor registration",
                    "solver": "simplified WLS diagnostic",
                    "source": "reports/tables/04_static_tag_transfer_summary.csv",
                }
            )

    prod_probe = safe_read(
        data_dir
        / "Analysis"
        / "official_extra_analysis"
        / "FULL_4way_comparison"
        / "production_method_probe"
        / "production_static_method_probe"
        / "tables"
        / "production_static_method_probe_summary.csv"
    )
    if not prod_probe.empty:
        for _, r in prod_probe.iterrows():
            label = str(r.get("label", r.get("case_id", "")))
            rows.append(
                {
                    "case": label,
                    "median_3d_mm": float(r["err_3d_median_mm"]),
                    "rms_3d_mm": float(r.get("err_3d_rms_mm", r.get("err_3d_rmse_mm"))),
                    "point_estimator": "mean" if "mean" in label or "current" in label else "median",
                    "registration": "official anchor-locked height-preserving",
                    "solver": "production C-core T4/T1 replay",
                    "source": "production_static_method_probe_summary.csv",
                }
            )

    raw_replay = safe_read(
        data_dir
        / "Analysis"
        / "official_extra_analysis"
        / "FULL_4way_comparison"
        / "production_method_probe"
        / "static_v4io_T1_T4_rerun"
        / "tables"
        / "tag_raw_replay_accuracy_summary.csv"
    )
    if not raw_replay.empty:
        for _, r in raw_replay.iterrows():
            if str(r.get("version")) == "v4-io" and str(r.get("tag_method")) in {"T1", "T4"}:
                rows.append(
                    {
                        "case": f"raw_replay_{r['version']}_{r['tag_method']}_session_median_summary",
                        "median_3d_mm": float(r["err_3d_median_mm"]),
                        "rms_3d_mm": float(r["err_3d_rms_mm"]),
                        "point_estimator": "session median summary",
                        "registration": "official anchor-locked height-preserving",
                        "solver": "C-core raw replay matrix",
                        "source": "static_v4io_T1_T4_rerun/tables/tag_raw_replay_accuracy_summary.csv",
                    }
                )

    published = safe_read(
        data_dir
        / "Analysis"
        / "official_extra_analysis"
        / "FULL_4way_comparison"
        / "production_method_probe"
        / "production_static_method_real_run_eval"
        / "tables"
        / "tag_accuracy_summary.csv"
    )
    if not published.empty:
        g = published[(published["version"].astype(str) == "v4-io") & (published["eval_set"].astype(str) == "all8")]
        if not g.empty:
            r = g.iloc[0]
            rows.append(
                {
                    "case": "published_production_static_v4io_all8",
                    "median_3d_mm": float(r["err_3d_median_mm"]),
                    "rms_3d_mm": float(r["err_3d_rms_mm"]),
                    "point_estimator": "mean",
                    "registration": "official anchor-locked height-preserving",
                    "solver": "published production static run",
                    "source": "production_static_method_real_run_eval/tables/tag_accuracy_summary.csv",
                }
            )

    candidate_rows.extend(
        [
            {
                "candidate_difference": "tag solver",
                "tested_evidence": "production C-core T4 mean reproduces 72.7/109.8; Phase 2 WLS gives 82.4/144.2",
                "effect": "primary cause; Phase 2 tag baseline is a relative diagnostic only",
            },
            {
                "candidate_difference": "point estimator",
                "tested_evidence": "T4 mean is 72.7/109.8 while T4 median is about 69.7/108.9",
                "effect": "changes median by a few mm, not the full Phase 2 mismatch",
            },
            {
                "candidate_difference": "registration",
                "tested_evidence": "published matrix uses anchor-locked height-preserving 2D horizontal alignment plus F/G/H vertical shift; Phase 2 WLS used its local rigid anchor fit",
                "effect": "must be matched for citable tag numbers",
            },
            {
                "candidate_difference": "valid-sample aggregation",
                "tested_evidence": "published C-core T4 solves raw frames; Phase 2 WLS solves one median range vector per static position",
                "effect": "contributes to RMS and p95 differences",
            },
        ]
    )
    return rows, candidate_rows


def add_tag_geometry_covariates(link_df: pd.DataFrame, anchor_truth: dict[str, np.ndarray], tag_truth: dict[str, np.ndarray]) -> pd.DataFrame:
    df = link_df.copy()
    horizontal = []
    vertical = []
    elevation = []
    for _, row in df.iterrows():
        anchor = str(row["anchor"])
        position = str(row["position"])
        a = anchor_truth[anchor]
        t = tag_truth[position]
        d = a - t
        h = float(math.hypot(d[0], d[2]))
        v = float(abs(d[1]))
        horizontal.append(h)
        vertical.append(v)
        elevation.append(float(math.degrees(math.atan2(v, max(h, 1e-9)))))
    df["horizontal_distance_mm"] = horizontal
    df["vertical_abs_mm"] = vertical
    df["elevation_angle_deg"] = elevation
    return df


def fit_fixed_effect_model(df: pd.DataFrame, covariates: list[str], categorical: list[str] | None = None) -> tuple[dict, pd.DataFrame]:
    categorical = categorical or []
    cols = [np.ones(len(df), dtype=float)]
    names = ["intercept_delta_tag_over_2_plus_A_anchor_effect"]
    anchor_dummies = pd.get_dummies(df["anchor"].astype(str), prefix="anchor", dtype=float)
    for col in [f"anchor_{a}" for a in ANCHOR_LABELS[1:]]:
        vals = anchor_dummies[col].to_numpy(dtype=float) if col in anchor_dummies else np.zeros(len(df), dtype=float)
        cols.append(vals)
        names.append(col)
    for cov in covariates:
        cols.append(pd.to_numeric(df[cov], errors="coerce").fillna(0.0).to_numpy(dtype=float))
        names.append(cov)
    for cat in categorical:
        dummies = pd.get_dummies(df[cat].astype(str), prefix=cat, dtype=float)
        for col in sorted(dummies.columns)[1:]:
            cols.append(dummies[col].to_numpy(dtype=float))
            names.append(col)
    x = np.column_stack(cols)
    y = pd.to_numeric(df["bias_mm"], errors="coerce").to_numpy(dtype=float)
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    pred = x @ beta
    residual = y - pred
    sst = float(np.sum((y - np.mean(y)) ** 2))
    sse = float(np.sum(residual * residual))
    coef = {name: float(value) for name, value in zip(names, beta)}
    summary = {
        "model": "+".join(covariates + categorical) if (covariates or categorical) else "anchor_fixed_effects_only",
        "links": int(len(df)),
        "rms_mm": float(np.sqrt(np.mean(residual * residual))),
        "r2": float(1.0 - sse / sst) if sst > 0 else math.nan,
        "distance_slope_percent": float(coef.get("vicon_distance_mm", math.nan) * 100.0),
        "horizontal_slope_percent": float(coef.get("horizontal_distance_mm", math.nan) * 100.0),
        "vertical_slope_percent": float(coef.get("vertical_abs_mm", math.nan) * 100.0),
        "elevation_slope_mm_per_deg": float(coef.get("elevation_angle_deg", math.nan)),
        "height_mid_effect_mm": float(coef.get("height_mid", math.nan)),
        "height_high_effect_mm": float(coef.get("height_high", math.nan)),
    }
    pred_df = df[["position", "anchor", "bias_mm", "vicon_distance_mm", "horizontal_distance_mm", "vertical_abs_mm", "elevation_angle_deg", "height"]].copy()
    pred_df["model"] = summary["model"]
    pred_df["pred_mm"] = pred
    pred_df["residual_mm"] = residual
    return summary, pred_df


def tag_bias_geometry(link_df: pd.DataFrame, anchor_truth: dict[str, np.ndarray], tag_truth: dict[str, np.ndarray]) -> tuple[list[dict], pd.DataFrame]:
    geom = add_tag_geometry_covariates(link_df, anchor_truth, tag_truth)
    specs = [
        ([], []),
        (["vicon_distance_mm"], []),
        (["horizontal_distance_mm"], []),
        (["vertical_abs_mm"], []),
        (["elevation_angle_deg"], []),
        ([], ["height"]),
        (["horizontal_distance_mm", "vertical_abs_mm"], []),
        (["horizontal_distance_mm", "elevation_angle_deg"], []),
        (["vicon_distance_mm", "elevation_angle_deg"], []),
        (["horizontal_distance_mm"], ["height"]),
        (["elevation_angle_deg"], ["height"]),
    ]
    rows: list[dict] = []
    preds = []
    for covs, cats in specs:
        summary, pred_df = fit_fixed_effect_model(geom, covs, cats)
        rows.append(summary)
        preds.append(pred_df)
    return rows, pd.concat(preds, ignore_index=True)


def plot_tag_bias_elevation(path: Path, pred_df: pd.DataFrame) -> None:
    base = pred_df[pred_df["model"] == "anchor_fixed_effects_only"].copy()
    if base.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.8))
    for height, g in base.groupby("height"):
        axes[0].scatter(g["elevation_angle_deg"], g["bias_mm"], s=28, alpha=0.75, label=str(height))
        axes[1].scatter(g["elevation_angle_deg"], g["residual_mm"], s=28, alpha=0.75, label=str(height))
    axes[0].set_ylabel("raw link bias [mm]")
    axes[1].set_ylabel("bias residual after anchor fixed effects [mm]")
    for ax in axes:
        ax.set_xlabel("absolute elevation angle [deg]")
        ax.grid(True, alpha=0.25)
    axes[0].set_title("Tag Bias vs Elevation")
    axes[1].set_title("Anchor-Additive Residual vs Elevation")
    axes[0].legend(title="height", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def fit_2d_rigid(src_xy: np.ndarray, dst_xy: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    src_c = src_xy.mean(axis=0)
    dst_c = dst_xy.mean(axis=0)
    x = src_xy - src_c
    y = dst_xy - dst_c
    u, _s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(2)
    if np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    t = dst_c - src_c @ r
    return r, t, float(np.linalg.det(r))


def fit_height_preserving(src: np.ndarray, dst: np.ndarray, labels: list[str]) -> tuple[np.ndarray, np.ndarray, float, float]:
    r2, t2, det2 = fit_2d_rigid(src[:, :2], dst[:, [0, 2]])
    ref_idx = [labels.index(a) for a in ("F", "G", "H") if a in labels]
    if not ref_idx:
        ref_idx = list(range(src.shape[0]))
    z_shift = float(np.mean(dst[ref_idx, 1] - src[ref_idx, 2]))
    return r2, t2, z_shift, det2


def apply_height_preserving(points: np.ndarray, r2: np.ndarray, t2: np.ndarray, z_shift: float) -> np.ndarray:
    xy = points[:, :2] @ r2 + t2
    return np.column_stack([xy[:, 0], points[:, 2] + z_shift, xy[:, 1]])


def write_tagfit_layout(template: dict, out_path: Path, delays: np.ndarray, label: str) -> None:
    obj = json.loads(json.dumps(template["data"]))
    obj["label"] = label
    obj["tag_delay_mm"] = 0.0
    for item in obj["anchors"]:
        aid = int(item["id"])
        item["d_anchor_mm"] = float(delays[aid])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def load_offline_solver(data_dir: Path):
    solver_root = data_dir.parent.parent / "biospur_tag_positioning_offline_solver"
    sys.path.insert(0, str(solver_root))
    from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames
    from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver
    from biospur_tag_positioning_offline_solver.layout_io import load_layout_json
    from biospur_tag_positioning_offline_solver.models import Frame, Observation, SolverConfig

    return read_tr_all_frames, TagPositionSolver, load_layout_json, SolverConfig, Frame, Observation


def summarize_solver_results(results: list[Any], estimator: str = "mean") -> dict:
    if not results:
        return {"status": "no_solution", "frames_solved": 0, "x_mm": math.nan, "y_mm": math.nan, "z_mm": math.nan}
    pts = np.asarray([[r.x_mm, r.y_mm, r.z_mm] for r in results], dtype=float)
    p = np.nanmean(pts, axis=0) if estimator == "mean" else np.nanmedian(pts, axis=0)
    d = pts - p[None, :]
    d3 = np.linalg.norm(d, axis=1)
    anchors_input = np.asarray([r.anchors_input for r in results], dtype=float)
    residual_rms = np.asarray([r.residual_rms_mm for r in results], dtype=float)
    return {
        "status": "ok",
        "frames_solved": int(len(results)),
        "x_mm": float(p[0]),
        "y_mm": float(p[1]),
        "z_mm": float(p[2]),
        "d3_std_mm": float(np.sqrt(np.nanmean(d3 * d3))),
        "pct_solved_ge8": float(np.mean(anchors_input >= 8.0) * 100.0),
        "residual_rms_median_mm": float(np.nanmedian(residual_rms)),
    }


def find_static_capture_dirs(data_dir: Path) -> dict[str, Path]:
    root = data_dir / "captures" / "erlangen_20260528_optitrack"
    out: dict[str, Path] = {}
    for p in sorted(root.glob("static_ID*_*/")):
        parts = p.name.split("_")
        if len(parts) >= 2 and parts[1].startswith("ID"):
            out[parts[1]] = p
    return out


def run_tagfit_upper_bound(
    data_dir: Path,
    out_dir: Path,
    layouts: dict[str, dict],
    link_df: pd.DataFrame,
    sweep_delta: dict[str, float],
    vicon_truth: dict[str, np.ndarray],
    tag_truth: dict[str, np.ndarray],
    workers: int,
) -> tuple[list[dict], list[dict]]:
    read_tr_all_frames, TagPositionSolver, load_layout_json, SolverConfig, Frame, Observation = load_offline_solver(data_dir)
    sigma_path = data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "tables" / "anchor_sigma.json"
    capture_dirs = find_static_capture_dirs(data_dir)
    frames_by_pos = {
        pos: read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
        for pos, path in capture_dirs.items()
        if pos in set(link_df["position"].astype(str))
    }

    vb_template = layouts["V-B_calibrated"]
    class _LayoutLike:
        version = "V-B_tagfit_LOO_T4_upper_bound"

        def __init__(self, coords: np.ndarray):
            self.x = coords

    _summary, rigid_fit, _err_rows = alignment_metrics(_LayoutLike(vb_template["coords"]), vicon_truth)

    layout_dir = out_dir / "phase2_6_layouts" / "V-B_tagfit_LOO_T4"
    positions = sorted(frames_by_pos)
    rows: list[dict] = []

    def corrected_frames(position: str, additive_by_anchor: np.ndarray, rho: float) -> list[Any]:
        out = []
        denom = 1.0 + float(rho)
        for frame in frames_by_pos[position]:
            obs = []
            for item in frame.observations:
                corrected_range = (float(item.range_mm) - float(additive_by_anchor[item.anchor_id])) / denom
                obs.append(Observation(item.anchor_id, corrected_range, item.quality_percent, item.status))
            out.append(
                Frame(
                    tag=frame.tag,
                    sweep=frame.sweep,
                    host_elapsed_s=frame.host_elapsed_s,
                    host_epoch_s=frame.host_epoch_s,
                    observations=tuple(obs),
                    imu=frame.imu,
                )
            )
        return out

    def solve_mode(position: str, fit: Any, mode: str, additive_by_anchor: np.ndarray) -> dict:
        if mode == "delta_only":
            variant = "V-B_tagfit_delta_only_LOO_T4"
            per_anchor_delay = additive_by_anchor
            solve_frames = frames_by_pos[position]
            correction_description = "layout d_anchor = 0.5*Delta_i + 0.5*Delta_tag"
        elif mode == "delta_plus_rho":
            variant = "V-B_tagfit_delta_plus_rho_LOO_T4"
            per_anchor_delay = np.zeros(8, dtype=float)
            solve_frames = corrected_frames(position, additive_by_anchor, fit.rho)
            correction_description = "range' = (range - additive)/(1+rho_tag)"
        else:
            raise ValueError(mode)
        layout_path = layout_dir / mode / f"{position}.json"
        write_tagfit_layout(vb_template, layout_path, per_anchor_delay, f"{variant} {position}")
        layout = load_layout_json(layout_path, sigma_path)
        solver = TagPositionSolver(layout, SolverConfig(method="T4"))
        results = []
        for frame in solve_frames:
            result = solver.solve_frame(frame)
            if result is not None:
                results.append(result)
        solved = summarize_solver_results(results, "mean")
        local = np.asarray([[solved["x_mm"], solved["y_mm"], solved["z_mm"]]], dtype=float)
        aligned = rigid_fit.apply(local)[0]
        truth = tag_truth[position]
        err = aligned - truth
        return {
            "variant": variant,
            "position": position,
            "frames_input": int(len(frames_by_pos[position])),
            "frames_solved": int(solved["frames_solved"]),
            "delta_tag_train_mm": float(fit.delta_tag_mm),
            "rho_tag_train_percent": float(fit.rho * 100.0),
            "anchor_delay_min_mm": float(np.min(per_anchor_delay)),
            "anchor_delay_max_mm": float(np.max(per_anchor_delay)),
            "correction_description": correction_description,
            "anchor_registration": "3D rigid/reflection fitted from anchors only",
            "anchor_registration_det": float(rigid_fit.det),
            "err_x_mm": float(err[0]),
            "err_y_vertical_mm": float(err[1]),
            "err_z_mm": float(err[2]),
            "err_horizontal_mm": float(math.hypot(err[0], err[2])),
            "err_vertical_mm": float(abs(err[1])),
            "err_3d_mm": float(np.linalg.norm(err)),
            "d3_std_mm": float(solved["d3_std_mm"]),
            "pct_solved_ge8": float(solved["pct_solved_ge8"]),
            "residual_rms_median_mm": float(solved["residual_rms_median_mm"]),
        }

    def run_one(position: str) -> list[dict]:
        train = link_df[link_df["position"].astype(str) != position].reset_index(drop=True)
        fit = fit_tag_bias_model(f"loo_without_{position}", train, sweep_delta)
        additive_by_anchor = 0.5 * fit.anchor_deltas_mm + 0.5 * fit.delta_tag_mm
        return [
            solve_mode(position, fit, "delta_only", additive_by_anchor),
            solve_mode(position, fit, "delta_plus_rho", additive_by_anchor),
        ]

    max_workers = max(1, min(workers, len(positions)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(run_one, pos): pos for pos in positions}
        for fut in as_completed(futs):
            rows.extend(fut.result())
    rows.sort(key=lambda r: r["position"])

    summary = []
    for variant, g in pd.DataFrame(rows).groupby("variant", sort=False):
        vals = g["err_3d_mm"].to_numpy(dtype=float)
        summary.append(
            {
            "variant": variant,
            "positions": int(len(g)),
            "static_tag_median_3d_mm": float(np.nanmedian(vals)),
            "static_tag_rmse_3d_mm": float(np.sqrt(np.nanmean(vals * vals))),
            "static_tag_p95_3d_mm": float(np.nanpercentile(vals, 95)),
            "static_tag_max_3d_mm": float(np.nanmax(vals)),
            "point_estimator": "mean",
            "registration": "anchor-only 3D rigid/reflection",
            "solver": "C-core T4, V-B layout, LOO tag-fit calibration",
            }
        )
    return rows, summary


def plot_upper_bound(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    positions = sorted(df["position"].astype(str).unique())
    variants = list(dict.fromkeys(df["variant"].astype(str).tolist()))
    x = np.arange(len(positions))
    width = 0.34 if len(variants) > 1 else 0.65
    colors = ["#8f5a4c", "#5a8f71", "#4c78a8"]
    for k, variant in enumerate(variants):
        vals = []
        for pos in positions:
            g = df[(df["variant"] == variant) & (df["position"] == pos)]
            vals.append(float(g["err_3d_mm"].iloc[0]) if len(g) else math.nan)
        offset = (k - (len(variants) - 1) / 2.0) * width
        ax.bar(x + offset, vals, width=width, label=variant, color=colors[k % len(colors)])
    ax.set_xticks(x)
    ax.set_xticklabels(positions, rotation=45, ha="right")
    ax.set_ylabel("3D static tag error [mm]")
    ax.set_title("V-B Layout with Leave-One-Position-Out Tag-Fit Delays (T4)")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def build_report(
    out_dir: Path,
    scale_summary: list[dict],
    va_summary: dict,
    baseline_rows: list[dict],
    baseline_candidates: list[dict],
    geom_rows: list[dict],
    upper_summary: list[dict],
    figs: dict[str, str],
) -> None:
    lines: list[str] = ["# Phase 2.6 Diagnostics Closure\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append("- Ground-truth terminology: `Vicon`")
    lines.append("- Scope: diagnostics closure only; no production solver files were modified.")
    lines.append("")

    vb = next(r for r in scale_summary if r["variant"] == "V-B_calibrated")
    baseline = next(r for r in scale_summary if r["variant"] == "baseline_v4io")
    collapsed = vb["count_positive"] < baseline["count_positive"]
    lines.append("## 2.6a Pairwise Scale Diagnostic")
    lines.append(
        f"V-B changes the positive-scale count from `{baseline['count_positive']}/28` to `{vb['count_positive']}/28`; "
        f"success check: **{'YES' if collapsed else 'NO'}**."
    )
    lines.append("")
    lines.append(markdown_table(scale_summary, ["variant", "pairs", "median_scale_error_percent", "count_positive", "count_negative", "count_abs_lt_1_percent", "rms_scale_error_percent"]))
    lines.append("")
    lines.append(f"![Pairwise scale-error histogram](figures/{figs['scale_hist']})")
    lines.append("")

    lines.append("## 2.6b V-A Delay Sanity")
    lines.append(
        "The original V-A residual convention is `distance + d_i + d_j - measured`, with `d_A=0`. "
        "Against the sweep additive fit, the expected relative delay is `(Delta_i-Delta_A)/2`."
    )
    lines.append("")
    lines.append(markdown_table([va_summary], ["convention_in_solver_residual", "expected_relative_formula", "non_A_sign_matches", "non_A_sign_disagrees", "rerun_triggered", "sign_fixed_anchor_median_3d_mm", "sign_fixed_anchor_rms_3d_mm", "sign_fixed_shape_rms_mm", "sign_fixed_pair_rms_mm"]))
    lines.append("")
    lines.append(f"![V-A delay sanity](figures/{figs['va_delay']})")
    lines.append("")

    lines.append("## 2.6c Tag Baseline Fidelity")
    lines.append(
        "The published static tag baseline is reproduced by the production-style C-core T4 mean path. "
        "The Phase 2 tag baseline was a simplified WLS diagnostic and is not citable as an absolute reproduction of the published T4 row."
    )
    lines.append("")
    lines.append(markdown_table(baseline_rows, ["case", "median_3d_mm", "rms_3d_mm", "point_estimator", "registration", "solver", "source"]))
    lines.append("")
    lines.append(markdown_table(baseline_candidates, ["candidate_difference", "tested_evidence", "effect"]))
    lines.append("")

    lines.append("## 2.6d Tag Bias Geometry")
    best = min(geom_rows, key=lambda r: r["rms_mm"])
    lines.append(
        f"Best single closure model here is `{best['model']}` with RMS `{best['rms_mm']:.1f}` mm. "
        "All models include an intercept plus per-anchor fixed effects, so the comparison is after tag/anchor additive terms."
    )
    lines.append(
        "Horizontal distance alone does not absorb the tag-side slope. Vertical separation/elevation is the stronger single covariate, "
        "and adding elevation to the distance model reduces the distance slope from `7.717%` to `4.635%`."
    )
    lines.append("")
    lines.append(markdown_table(geom_rows, ["model", "links", "rms_mm", "r2", "distance_slope_percent", "horizontal_slope_percent", "vertical_slope_percent", "elevation_slope_mm_per_deg", "height_mid_effect_mm", "height_high_effect_mm"]))
    lines.append("")
    lines.append(f"![Tag bias vs elevation](figures/{figs['tag_elevation']})")
    lines.append("")

    lines.append("## 2.6e V-B Tag-Fit Upper Bound")
    lines.append(
        "This run uses the V-B anchor layout, production C-core T4, the same anchor-only 3D rigid/reflection registration used by the Phase 2 solver diagnostics, "
        "and leave-one-position-out tag-fitted calibration. `delta_only` applies only the additive terms as layout delays; `delta_plus_rho` also applies the fitted proportional term by range correction."
    )
    lines.append("")
    lines.append(markdown_table(upper_summary, ["variant", "positions", "static_tag_median_3d_mm", "static_tag_rmse_3d_mm", "static_tag_p95_3d_mm", "static_tag_max_3d_mm", "point_estimator", "registration", "solver"]))
    lines.append("")
    lines.append(f"![Upper-bound tag errors](figures/{figs['upper_bound']})")
    lines.append("")
    lines.append("STOP: Phase 2.6 diagnostics closure only. Do not proceed to solver integration or production changes until this report is reviewed.")
    (out_dir / "02_6_diagnostics_closure.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    out_dir = args.out_dir.resolve()
    tables_dir = out_dir / "tables"
    figures_dir = out_dir / "figures"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    phase1 = load_phase1_data(data_dir, out_dir)
    primary_truth = load_primary_vicon_anchor_truth(data_dir, phase1.anchor_truth)
    median_anchor_truth = anchor_coord_map(phase1.anchor_truth)
    tag_truth = tag_coord_map(phase1.tag_truth)
    sweep_delta = load_sweep_deltas(out_dir)

    layout_root = out_dir / "phase2_solver_layouts"
    layouts = {variant: load_layout_json_plain(layout_root / variant / "layout.json") for variant in VARIANT_ORDER}

    scale_rows, scale_summary = pairwise_scale_diagnostic(layouts, primary_truth)
    write_csv(tables_dir / "05_pairwise_scale_errors.csv", scale_rows)
    write_csv(tables_dir / "05_pairwise_scale_summary.csv", scale_summary)
    scale_fig = figures_dir / "05_pairwise_scale_error_hist.png"
    plot_scale_hist(scale_fig, scale_rows)

    va_rows, va_summary, _reruns = va_delay_sanity(data_dir, out_dir, layouts, primary_truth)
    write_csv(tables_dir / "05_va_delay_sanity.csv", va_rows)
    write_csv(tables_dir / "05_va_delay_sanity_summary.csv", [va_summary])
    va_fig = figures_dir / "05_va_delay_sanity_scatter.png"
    plot_va_delay(va_fig, va_rows)

    baseline_rows, baseline_candidate_rows = tag_baseline_fidelity(data_dir, out_dir)
    write_csv(tables_dir / "05_tag_baseline_fidelity.csv", baseline_rows)
    write_csv(tables_dir / "05_tag_baseline_fidelity_candidates.csv", baseline_candidate_rows)

    link_df = pd.read_csv(tables_dir / "03_tag_link_bias_links.csv")
    link_df["position"] = link_df["position"].astype(str)
    link_df["anchor"] = link_df["anchor"].astype(str)
    geom_rows, geom_preds = tag_bias_geometry(link_df, median_anchor_truth, tag_truth)
    write_csv(tables_dir / "05_tag_bias_geometry_models.csv", geom_rows)
    geom_preds.to_csv(tables_dir / "05_tag_bias_geometry_predictions.csv", index=False)
    elevation_fig = figures_dir / "05_tag_bias_vs_elevation.png"
    plot_tag_bias_elevation(elevation_fig, geom_preds)

    upper_rows, upper_summary = run_tagfit_upper_bound(
        data_dir,
        out_dir,
        layouts,
        link_df,
        sweep_delta,
        primary_truth,
        tag_truth,
        args.workers,
    )
    write_csv(tables_dir / "05_vb_tagfit_upper_bound_positions.csv", upper_rows)
    write_csv(tables_dir / "05_vb_tagfit_upper_bound_summary.csv", upper_summary)
    upper_fig = figures_dir / "05_vb_tagfit_upper_bound_errors.png"
    plot_upper_bound(upper_fig, upper_rows)

    build_report(
        out_dir,
        scale_summary,
        va_summary,
        baseline_rows,
        baseline_candidate_rows,
        geom_rows,
        upper_summary,
        {
            "scale_hist": scale_fig.name,
            "va_delay": va_fig.name,
            "tag_elevation": elevation_fig.name,
            "upper_bound": upper_fig.name,
        },
    )
    print(f"Phase 2.6 report written: {out_dir / '02_6_diagnostics_closure.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
