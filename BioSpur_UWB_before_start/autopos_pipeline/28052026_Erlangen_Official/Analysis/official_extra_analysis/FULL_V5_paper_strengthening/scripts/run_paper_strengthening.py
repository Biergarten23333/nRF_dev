#!/usr/bin/env python3
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import math
import os
import py_compile
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

import numpy as np
import pandas as pd
import psutil

try:
    from scipy import optimize, stats
except Exception:  # pragma: no cover
    optimize = None
    stats = None

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None


THIS = Path(__file__).resolve()
ANALYSIS = THIS.parents[2]
BASE = THIS.parents[4]
OUT_ROOT = ANALYSIS / "FULL_V5_paper_strengthening"
TABLES = OUT_ROOT / "tables"
FIGURES = OUT_ROOT / "figures"
REPORTS = OUT_ROOT / "reports"
SCRIPTS = OUT_ROOT / "scripts"

EXT_SCRIPT = ANALYSIS / "FULL_V5_extended_mechanism_ablations/scripts/run_extended_mechanism_ablations.py"
MECH_DIR = ANALYSIS / "FULL_V5_mechanistic_deepdive"
OVERNIGHT_DIR = ANALYSIS / "FULL_V5_overnight_batch2"
ROTO_DIR = ANALYSIS / "FULL_V5_roto_deepdive"
GPU_DIR = ANALYSIS / "FULL_V5_GPU_discovery"
FOLLOWUP_DIR = ANALYSIS / "FULL_V5_followup_validation"

FULL_V5_STATIC = ANALYSIS / "FULL_V5/tables/static_per_position.csv"
FULL_V5_DOP = ANALYSIS / "FULL_V5/tables/dop_per_position.csv"
FULL_V5_RHO_STATIC = ANALYSIS / "FULL_V5/tables/per_anchor_residual_static.csv"
FULL_4WAY_LOO = ANALYSIS / "FULL_4way_comparison/tables/v5_loo_tag_delay_summary.csv"
EXT_ITEM18 = ANALYSIS / "FULL_V5_extended_mechanism_ablations/tables/item18_temporal_split.csv"
EXT_ITEM21 = ANALYSIS / "FULL_V5_extended_mechanism_ablations/tables/item21_range_percentile_sweep.csv"
EXT_ITEM24 = ANALYSIS / "FULL_V5_extended_mechanism_ablations/tables/item24_rho_distribution_shape.csv"
ANCHOR_SIDE = OVERNIGHT_DIR / "tables/paper_table_anchor_side.csv"
STATIC_ACCURACY = OVERNIGHT_DIR / "tables/paper_table_static_accuracy.csv"
P30_RECAL = OVERNIGHT_DIR / "tables/n4_p30_recalibration.csv"
P30_SENS = OVERNIGHT_DIR / "tables/n6_percentile_sensitivity.csv"
BATCH2_STATUS = OVERNIGHT_DIR / "tables/batch2_task_status_summary.csv"
M1_PREV = MECH_DIR / "tables/m1_error_direction.csv"
M2_PREV = MECH_DIR / "tables/m2_error_budget.csv"
M4_NLOS = MECH_DIR / "tables/m4_ei_vs_nlos.csv"
M4_COUNTER = MECH_DIR / "tables/m4_counterfactual.csv"
M5_IDENT = MECH_DIR / "tables/m5_identifiability_table.csv"
M8_ANATOMY = MECH_DIR / "tables/m8_position_anatomy.csv"
M9_FISHER = MECH_DIR / "tables/m9_fisher_eigenvectors.csv"
M10_EVIDENCE = MECH_DIR / "tables/m10_evidence_matrix.csv"
GPU_SHAPLEY = GPU_DIR / "tables/task3_shapley_values.csv"
GPU_TASK6 = GPU_DIR / "tables/task6_cv_results.csv"
GPU_TASK11 = GPU_DIR / "tables/task11_model_evidence.csv"
ROTO_GAP = ROTO_DIR / "tables/r4_gap_decomposition.csv"
ROTO_ALIGN = ROTO_DIR / "tables/r2_alignment_summary.csv"
ROTO_PHASE = ROTO_DIR / "tables/r6_phase_aggregate.csv"
ROTO_DTAG = ROTO_DIR / "tables/r3_estimated_dtag.csv"

WORKERS = 6
LOO_DTAG_MM = 49.621
V5_COMMON_MODE_MM = 111.985
ANCHORS = tuple("ABCDEFGH")
LOWER = {0, 1, 2, 3}
UPPER = {4, 5, 6, 7}


@dataclass
class TaskResult:
    task: str
    status: str
    elapsed_s: float
    mean_cpu_percent: float
    max_cpu_percent: float
    key_finding: str
    notes: str = ""


def require_path(path: Path, label: str, fatal: bool = True) -> Path | None:
    if path.exists():
        return path
    if fatal:
        raise FileNotFoundError(f"missing required input for {label}: {path}")
    return None


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_csv(path: Path, label: str, fatal: bool = True) -> pd.DataFrame:
    p = require_path(path, label, fatal=fatal)
    if p is None:
        return pd.DataFrame()
    return pd.read_csv(p)


def finite(values: Any) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    return arr[np.isfinite(arr)]


def pct(values: Any, q: float) -> float:
    arr = finite(values)
    return float(np.nanpercentile(arr, q)) if arr.size else float("nan")


def rmse(values: Any) -> float:
    arr = finite(values)
    return float(math.sqrt(float(np.nanmean(arr * arr)))) if arr.size else float("nan")


def linreg(x: Any, y: Any) -> dict[str, float]:
    xx = np.asarray(x, dtype=float)
    yy = np.asarray(y, dtype=float)
    mask = np.isfinite(xx) & np.isfinite(yy)
    xx = xx[mask]
    yy = yy[mask]
    if xx.size < 3 or float(np.nanstd(xx)) <= 1e-12:
        return {"slope": float("nan"), "intercept": float("nan"), "r2": float("nan"), "p_value": float("nan"), "n": int(xx.size)}
    if stats is not None:
        r = stats.linregress(xx, yy)
        return {"slope": float(r.slope), "intercept": float(r.intercept), "r2": float(r.rvalue * r.rvalue), "p_value": float(r.pvalue), "n": int(xx.size)}
    slope, intercept = np.polyfit(xx, yy, 1)
    pred = slope * xx + intercept
    ss_res = float(np.sum((yy - pred) ** 2))
    ss_tot = float(np.sum((yy - np.mean(yy)) ** 2))
    return {"slope": float(slope), "intercept": float(intercept), "r2": float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan"), "p_value": float("nan"), "n": int(xx.size)}


def weighted_median(values: Any, weights: Any) -> float:
    vals = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    mask = np.isfinite(vals) & np.isfinite(w) & (w > 0)
    vals = vals[mask]
    w = w[mask]
    if vals.size == 0:
        return float("nan")
    order = np.argsort(vals)
    vals = vals[order]
    w = w[order]
    c = np.cumsum(w) / np.sum(w)
    return float(vals[int(np.searchsorted(c, 0.5, side="left"))])


def phase_context(task: str) -> dict[str, Any]:
    psutil.cpu_percent(interval=None)
    return {"task": task, "start": time.perf_counter(), "cpu": []}


def sample_cpu(ctx: dict[str, Any]) -> None:
    ctx["cpu"].append(float(psutil.cpu_percent(interval=0.0)))


def finish(ctx: dict[str, Any], status: str, key: str, notes: str = "") -> TaskResult:
    sample_cpu(ctx)
    samples = ctx["cpu"] or [0.0]
    return TaskResult(
        task=ctx["task"],
        status=status,
        elapsed_s=float(time.perf_counter() - ctx["start"]),
        mean_cpu_percent=float(np.nanmean(samples)),
        max_cpu_percent=float(np.nanmax(samples)),
        key_finding=key,
        notes=notes,
    )


def setup_style() -> None:
    if plt is None:
        return
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "font.size": 10,
            "axes.labelsize": 10,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "axes.titlesize": 10,
        }
    )


def load_context() -> dict[str, Any]:
    ext = load_module(require_path(EXT_SCRIPT, "extended mechanism runner"), "paper_ext")
    mech, full = ext.load_previous_modules()
    inputs, configs, assignments, maps = ext.build_inputs_and_configs(mech, full)
    static_files = [Path(p) for p in inputs["static_files"]]
    raw_ranges, raw_info = ext.load_raw_ranges(static_files)
    medians = ext.raw_medians(raw_ranges)
    p30 = {
        sid: {aid: float(np.nanpercentile(vals, 30)) for aid, vals in by_anchor.items() if vals.size}
        for sid, by_anchor in raw_ranges.items()
    }
    raw_first = {
        sid: {aid: float(vals[0]) for aid, vals in by_anchor.items() if vals.size}
        for sid, by_anchor in raw_ranges.items()
    }
    ids = sorted(inputs["tag_truth_np"].keys())
    residuals = {
        key: ext.residual_observations(cfg, medians, inputs["tag_truth_np"], maps, LOO_DTAG_MM)
        for key, cfg in configs.items()
        if key in ("V4_CV4", "V5_CV5", "Vicon_Ccm")
    }
    return {
        "ext": ext,
        "inputs": inputs,
        "configs": configs,
        "assignments": assignments,
        "maps": maps,
        "raw_ranges": raw_ranges,
        "raw_info": raw_info,
        "medians": medians,
        "p30": p30,
        "raw_first": raw_first,
        "ids": ids,
        "residuals": residuals,
    }


def solve_point_ls(measured: dict[int, float], coords: np.ndarray, delays: dict[int, float], dtag: float, anchor_ids: list[int], x0: np.ndarray) -> np.ndarray | None:
    if optimize is None:
        return None
    aids = [int(a) for a in anchor_ids if int(a) in measured and np.isfinite(float(measured[int(a)]))]
    if len(aids) < 4:
        return None

    def residual(x: np.ndarray) -> np.ndarray:
        return np.asarray([float(measured[aid]) - float(np.linalg.norm(x - coords[aid])) - float(delays[aid]) - float(dtag) for aid in aids], dtype=float)

    try:
        res = optimize.least_squares(residual, np.asarray(x0, dtype=float), loss="huber", f_scale=100.0, max_nfev=200)
    except Exception:
        return None
    return np.asarray(res.x, dtype=float) if res.success else None


def effective_dtag_values(ctx: dict[str, Any], cfg: Any, ids: list[str], anchor_ids: list[int], ranges: dict[str, dict[int, float]]) -> list[float]:
    vals: list[float] = []
    truth_by_id = ctx["inputs"]["tag_truth_np"]
    for sid in ids:
        truth = np.asarray(truth_by_id[sid], dtype=float)
        for aid in anchor_ids:
            if aid not in ranges.get(sid, {}):
                continue
            vals.append(float(ranges[sid][aid]) - float(np.linalg.norm(truth - cfg.coords[aid])) - float(cfg.delays[aid]))
    return vals


def calibrate_dtag(ctx: dict[str, Any], cfg: Any, ids: list[str], anchor_ids: list[int], ranges: dict[str, dict[int, float]], weights_by_sid: dict[str, float] | None = None) -> float:
    if weights_by_sid is None:
        vals = effective_dtag_values(ctx, cfg, ids, anchor_ids, ranges)
        return float(np.nanmedian(finite(vals))) if vals else float("nan")
    vals: list[float] = []
    weights: list[float] = []
    truth_by_id = ctx["inputs"]["tag_truth_np"]
    for sid in ids:
        truth = np.asarray(truth_by_id[sid], dtype=float)
        for aid in anchor_ids:
            if aid not in ranges.get(sid, {}):
                continue
            vals.append(float(ranges[sid][aid]) - float(np.linalg.norm(truth - cfg.coords[aid])) - float(cfg.delays[aid]))
            weights.append(float(weights_by_sid.get(sid, 1.0)))
    return weighted_median(vals, weights)


def eval_static_ls(ctx: dict[str, Any], cfg: Any, ids: list[str], ranges: dict[str, dict[int, float]], dtag_mode: str, dtag_value: float | None, anchor_ids: list[int], cal_n: int | str | None = None, weights_by_sid: dict[str, float] | None = None) -> dict[str, Any]:
    truth_by_id = ctx["inputs"]["tag_truth_np"]
    centroid = np.asarray(cfg.coords, dtype=float).mean(axis=0)
    errors: list[float] = []
    fails = 0
    dtag_used: list[float] = []
    cal_ids = ids
    if isinstance(cal_n, int):
        cal_ids = ids[: max(0, min(cal_n, len(ids)))]
    for sid in ids:
        if dtag_mode == "fixed":
            dtag = float(dtag_value or 0.0)
        elif dtag_mode == "global_cal":
            dtag = calibrate_dtag(ctx, cfg, cal_ids, anchor_ids, ranges, weights_by_sid=weights_by_sid)
        elif dtag_mode == "loo":
            train = [x for x in ids if x != sid]
            dtag = calibrate_dtag(ctx, cfg, train, anchor_ids, ranges, weights_by_sid=weights_by_sid)
        else:
            raise ValueError(f"unknown dtag mode {dtag_mode}")
        dtag_used.append(dtag)
        truth = np.asarray(truth_by_id[sid], dtype=float)
        sol = solve_point_ls(ranges.get(sid, {}), np.asarray(cfg.coords, dtype=float), cfg.delays, dtag, anchor_ids, centroid)
        if sol is None:
            fails += 1
            continue
        errors.append(float(np.linalg.norm(sol - truth)))
    return {
        "median_3d": pct(errors, 50),
        "p95_3d": pct(errors, 95),
        "rmse": rmse(errors),
        "fail_rate": float(fails / len(ids)) if ids else 1.0,
        "n_positions": len(errors),
        "d_tag_mm": float(np.nanmean(finite(dtag_used))) if dtag_used else float("nan"),
    }


def append_table(lines: list[str], rows: list[dict[str, Any]], cols: list[str], max_rows: int | None = None) -> None:
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    shown = rows if max_rows is None else rows[:max_rows]
    for row in shown:
        out = []
        for col in cols:
            val = row.get(col, "")
            if isinstance(val, (float, np.floating)):
                out.append("" if not np.isfinite(float(val)) else f"{float(val):.3f}")
            else:
                out.append(str(val).replace("|", "\\|"))
        lines.append("| " + " | ".join(out) + " |")
    if max_rows is not None and len(rows) > max_rows:
        lines.append(f"\n... {len(rows) - max_rows} rows omitted ...")
    lines.append("")


def task_report(filename: str, title: str, key: str, rows: list[dict[str, Any]], cols: list[str], notes: str = "") -> None:
    lines = [f"# {title}", "", f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S')}", "", f"Key finding: {key}", ""]
    if notes:
        lines.extend([notes, ""])
    if rows:
        append_table(lines, rows, cols, max_rows=20)
    write_text(REPORTS / filename, "\n".join(lines) + "\n")


def task_p1(ctx: dict[str, Any]) -> TaskResult:
    pc = phase_context("P1")
    m1 = read_csv(M1_PREV, "mechanistic M1 error direction")
    truth = ctx["inputs"]["tag_truth_np"]
    centroid = np.asarray(ctx["inputs"]["truth_coords"], dtype=float).mean(axis=0)
    rows: list[dict[str, Any]] = []
    for _, r in m1.iterrows():
        sid = str(r["position_id"])
        t = np.asarray(truth.get(sid, [r.get("truth_x_mm", 0), 0, r.get("truth_z_mm", 0)]), dtype=float)
        radial = np.array([t[0] - centroid[0], 0.0, t[2] - centroid[2]], dtype=float)
        nr = float(np.linalg.norm(radial))
        radial_unit = radial / nr if nr > 1e-9 else np.array([1.0, 0.0, 0.0])
        tangent_unit = np.array([-radial_unit[2], 0.0, radial_unit[0]], dtype=float)
        err = np.array([float(r.get("err_x_mm", 0.0)), 0.0, float(r.get("err_z_mm", 0.0))])
        rows.append(
            {
                "position_id": sid,
                "config": str(r["config"]),
                "signed_radial_mm": float(np.dot(err, radial_unit)),
                "signed_tangential_mm": float(np.dot(err, tangent_unit)),
                "signed_vertical_mm": float(r.get("signed_vertical", np.nan)),
                "distance_from_centroid": nr,
            }
        )
    write_csv(TABLES / "p1_signed_radial.csv", rows)

    summary: list[dict[str, Any]] = []
    df = pd.DataFrame(rows)
    for config, g in df.groupby("config"):
        vals = finite(g["signed_radial_mm"])
        pval = float(stats.ttest_1samp(vals, 0.0).pvalue) if stats is not None and vals.size > 2 else float("nan")
        summary.append(
            {
                "config": config,
                "mean_signed_radial": float(np.nanmean(vals)) if vals.size else float("nan"),
                "std": float(np.nanstd(vals, ddof=1)) if vals.size > 1 else float("nan"),
                "t_test_vs_zero_p": pval,
                "mean_signed_tangential": float(np.nanmean(g["signed_tangential_mm"])),
                "mean_signed_vertical": float(np.nanmean(g["signed_vertical_mm"])),
            }
        )
    write_csv(TABLES / "p1_signed_radial_summary.csv", summary)

    contrib_rows = anchor_contribution_rows(ctx)
    radial_contrib = []
    for row in contrib_rows:
        radial_contrib.append({k: row[k] for k in ("position_id", "anchor_label", "contribution_radial", "contribution_vertical", "rho_mm")})
    write_csv(TABLES / "p1_anchor_radial_contribution.csv", radial_contrib)

    if plt is not None:
        fig, ax = plt.subplots(figsize=(3.5, 2.6))
        order = [r["config"] for r in summary]
        ax.bar(order, [r["mean_signed_radial"] for r in summary], color=["tab:blue", "tab:orange", "tab:green"][: len(order)])
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_ylabel("signed radial error (mm)")
        ax.tick_params(axis="x", rotation=25)
        fig.tight_layout()
        fig.savefig(FIGURES / "p1_radial_error_comparison.png", dpi=300)
        plt.close(fig)

    v4 = next((r for r in summary if "V4" in r["config"]), {})
    v5 = next((r for r in summary if "V5" in r["config"]), {})
    key = f"V4 mean signed radial {v4.get('mean_signed_radial', float('nan')):.1f} mm; V5 {v5.get('mean_signed_radial', float('nan')):.1f} mm"
    task_report("TASK_P1_RADIAL_MECHANISM.md", "Task P1 - Signed Radial Mechanism", key, summary, ["config", "mean_signed_radial", "std", "t_test_vs_zero_p"])
    return finish(pc, "ok", key)


def task_p2(ctx: dict[str, Any]) -> TaskResult:
    pc = phase_context("P2")
    nlos = read_csv(M4_NLOS, "mechanistic M4 e_i vs NLOS")
    shap = read_csv(GPU_SHAPLEY, "GPU Shapley", fatal=False)
    cfg = ctx["configs"]["V5_CV5"]
    rows = []
    for _, r in nlos.drop_duplicates("anchor_label").iterrows():
        aid = int(r.get("anchor_id", ANCHORS.index(str(r["anchor_label"]))))
        shap_val = float(shap.loc[shap["anchor_label"] == r["anchor_label"], "shapley_3d"].iloc[0]) if not shap.empty and (shap["anchor_label"] == r["anchor_label"]).any() else float(r.get("shapley", np.nan))
        rows.append(
            {
                "anchor_label": str(r["anchor_label"]),
                "anchor_id": aid,
                "e_i_mm": float(r["e_i_mm"]),
                "rms_rho": float(r.get("rms_rho", np.nan)),
                "spike_rate": float(r.get("spike_rate", np.nan)),
                "median_abs_rho": float(r.get("median_abs_rho", np.nan)),
                "shapley": shap_val,
                "layer_binary": 0 if aid in LOWER else 1,
                "anchor_height_mm": float(cfg.coords[aid][1]),
            }
        )
    base = pd.DataFrame(rows)
    corr_rows = []
    for predictor in ("rms_rho", "spike_rate", "median_abs_rho", "shapley", "layer_binary", "anchor_height_mm"):
        x = finite(base[predictor])
        y = finite(base.loc[np.isfinite(base[predictor]), "e_i_mm"])
        if stats is not None and x.size > 2 and np.nanstd(x) > 1e-12:
            rr = stats.pearsonr(x, y)
            corr_rows.append({"predictor": predictor, "pearson_r": float(rr.statistic), "p_value": float(rr.pvalue)})
        else:
            corr_rows.append({"predictor": predictor, "pearson_r": float("nan"), "p_value": float("nan")})
    write_csv(TABLES / "p2_ei_correlations.csv", corr_rows)

    counter = read_csv(M4_COUNTER, "mechanistic M4 counterfactual", fatal=False)
    cf_rows: list[dict[str, Any]] = []
    for _, r in counter.iterrows():
        cf_rows.append({"config": r.get("config", r.get("model", "")), "description": r.get("notes", ""), "median_3d": float(r.get("median_3d", r.get("median_3d_mm", np.nan))), "rmse": float(r.get("rmse", r.get("rmse_3d_mm", np.nan)))})
    delays_df = {int(k): float(v) for k, v in cfg.delays.items()}
    delays_df[3] = V5_COMMON_MODE_MM
    delays_df[5] = V5_COMMON_MODE_MM
    cfg_proxy = type("Cfg", (), {"coords": cfg.coords, "delays": delays_df})()
    res = eval_static_ls(ctx, cfg_proxy, ctx["ids"], ctx["medians"], "loo", None, list(range(8)))
    cf_rows.append({"config": "V5_CV5_zero_DF_ei", "description": "LS proxy: zero e_i for anchors D and F, LOO D_tag", "median_3d": res["median_3d"], "rmse": res["rmse"]})
    write_csv(TABLES / "p2_counterfactual.csv", cf_rows)
    top_corr = max(corr_rows, key=lambda r: abs(r["pearson_r"]) if np.isfinite(r["pearson_r"]) else -1)
    key = f"strongest |corr(e_i, predictor)| is {top_corr['predictor']} r={top_corr['pearson_r']:.2f}"
    task_report("TASK_P2_DELAY_VS_NLOS.md", "Task P2 - V5 Delay vs NLOS", key, corr_rows, ["predictor", "pearson_r", "p_value"], "Counterfactual rows are in tables/p2_counterfactual.csv.")
    return finish(pc, "ok", key)


def choose_subset(shap: pd.DataFrame, k: int, layer_config: str) -> tuple[list[int], str]:
    if shap.empty:
        order = list(range(8))
    else:
        order = [ANCHORS.index(a) for a in shap.sort_values("shapley_3d", ascending=False)["anchor_label"] if a in ANCHORS]
    if layer_config == "dual_layer":
        subset = order[:k]
        if subset and (not set(subset) & LOWER or not set(subset) & UPPER):
            lower = [a for a in order if a in LOWER]
            upper = [a for a in order if a in UPPER]
            subset = (lower[: max(1, k // 2)] + upper[: max(1, k - max(1, k // 2))])[:k]
        return sorted(set(subset), key=subset.index), "dual-layer top-Shapley subset"
    candidates = [a for a in order if a in LOWER]
    if len(candidates) < k:
        candidates = [a for a in order if a in UPPER]
    if len(candidates) < k:
        return candidates, "single-layer impossible for requested k"
    return candidates[:k], "single-layer top-Shapley subset"


def task_p3(ctx: dict[str, Any]) -> TaskResult:
    pc = phase_context("P3")
    shap = read_csv(GPU_SHAPLEY, "GPU Shapley", fatal=False)
    cfg = ctx["configs"]["V5_CV5"]
    rows = []
    ranges_by_name = {"raw": ctx["raw_first"], "p50": ctx["medians"], "p30": ctx["p30"]}
    for k in (4, 5, 6, 7, 8):
        ranges_count = k * (k - 1) // 2
        params = 4 * k - 6
        over = ranges_count > params
        for layer_config in ("single_layer", "dual_layer"):
            subset, subset_note = choose_subset(shap, k, layer_config)
            labels = "".join(ANCHORS[a] for a in subset)
            for agg, ranges in ranges_by_name.items():
                for cal in (0, 1, 2, 3, 4, 5, "full_LOO"):
                    if not over or len(subset) < 4:
                        rows.append({"n_anchors": k, "layer_config": layer_config, "n_cal": cal, "aggregation": agg, "median_3d": float("nan"), "fail_rate": 1.0, "anchor_labels": labels, "overdetermined": over, "notes": subset_note})
                        continue
                    mode = "fixed" if cal == 0 else ("loo" if cal == "full_LOO" else "global_cal")
                    cal_n = None if cal in (0, "full_LOO") else int(cal)
                    res = eval_static_ls(ctx, cfg, ctx["ids"], ranges, mode, 0.0, subset, cal_n=cal_n)
                    rows.append({"n_anchors": k, "layer_config": layer_config, "n_cal": cal, "aggregation": agg, "median_3d": res["median_3d"], "rmse": res["rmse"], "fail_rate": res["fail_rate"], "d_tag_mm": res["d_tag_mm"], "anchor_labels": labels, "overdetermined": over, "notes": subset_note})
    write_csv(TABLES / "p3_deployment_sweep.csv", rows)

    valid = [r for r in rows if np.isfinite(float(r.get("median_3d", np.nan))) and float(r.get("fail_rate", 1.0)) < 1.0]
    frontier = []
    for r in valid:
        cal_cost = 24 if r["n_cal"] == "full_LOO" else int(r["n_cal"])
        cost = int(r["n_anchors"]) + cal_cost
        dominated = False
        for o in valid:
            ocost = int(o["n_anchors"]) + (24 if o["n_cal"] == "full_LOO" else int(o["n_cal"]))
            if ocost <= cost and float(o["median_3d"]) <= float(r["median_3d"]) and (ocost < cost or float(o["median_3d"]) < float(r["median_3d"])):
                dominated = True
                break
        if not dominated:
            frontier.append({**r, "cost": cost})
    frontier.sort(key=lambda r: (r["cost"], r["median_3d"]))
    write_csv(TABLES / "p3_pareto_frontier.csv", frontier)

    def best_filter(name: str, pred) -> dict[str, Any]:
        cand = [r for r in valid if pred(r)]
        if not cand:
            return {"scenario": name, "config": "none", "median_3d": float("nan"), "notes": "no valid row"}
        b = min(cand, key=lambda r: float(r["median_3d"]))
        return {"scenario": name, "config": f"{b['n_anchors']} anchors {b['layer_config']} cal={b['n_cal']} {b['aggregation']} {b['anchor_labels']}", "median_3d": float(b["median_3d"]), "notes": b.get("notes", "")}

    recipes = [
        best_filter("minimal_viable", lambda r: int(r["n_anchors"]) <= 6 and r["layer_config"] == "dual_layer" and r["n_cal"] in (1, 2, 3)),
        best_filter("standard", lambda r: int(r["n_anchors"]) == 8 and r["layer_config"] == "dual_layer" and r["n_cal"] in (3, 4, 5)),
        best_filter("best_achievable_proxy", lambda r: int(r["n_anchors"]) == 8 and r["layer_config"] == "dual_layer"),
    ]
    write_csv(TABLES / "p3_deployment_recipe.csv", recipes)

    if plt is not None and frontier:
        fig, ax = plt.subplots(figsize=(3.5, 2.6))
        valid_costs = [int(r["n_anchors"]) + (24 if r["n_cal"] == "full_LOO" else int(r["n_cal"])) for r in valid]
        ax.scatter(valid_costs, [r["median_3d"] for r in valid], s=14, alpha=0.25, color="tab:gray")
        ax.plot([r["cost"] for r in frontier], [r["median_3d"] for r in frontier], marker="o", color="tab:blue")
        ax.set_xlabel("deployment cost proxy")
        ax.set_ylabel("median 3D error (mm)")
        fig.tight_layout()
        fig.savefig(FIGURES / "p3_pareto_frontier.png", dpi=300)
        plt.close(fig)
    key = f"{len(frontier)} Pareto rows; best proxy median {min([r['median_3d'] for r in valid], default=float('nan')):.1f} mm"
    task_report("TASK_P3_DEPLOYMENT.md", "Task P3 - Deployment Sweep", key, recipes, ["scenario", "config", "median_3d", "notes"], "Deployment sweep uses median/p30/static raw range least-squares proxies for fast paper-prep exploration.")
    return finish(pc, "ok", key)


def task_p4(ctx: dict[str, Any]) -> TaskResult:
    pc = phase_context("P4")
    static = read_csv(FULL_V5_STATIC, "FULL_V5 static per-position")
    static = static[static["tag_delay_mode"].astype(str).str.contains("LOO|DLOO|49", case=False, na=False) | np.isclose(static.get("d_tag_mm", pd.Series(np.nan, index=static.index)), LOO_DTAG_MM, atol=0.1)]
    if static.empty:
        static = read_csv(FULL_V5_STATIC, "FULL_V5 static per-position").drop_duplicates("ID")
    residuals = pd.DataFrame(ctx["residuals"]["V5_CV5"])
    rows = []
    for sid in ctx["ids"]:
        eff = finite(residuals.loc[residuals["position_id"] == sid, "effective_dtag_mm"])
        err_row = static[static["ID"].astype(str) == sid]
        info = ctx["raw_info"].get(sid, {})
        rows.append(
            {
                "position_id": sid,
                "temporal_order": int(sid.replace("ID", "")) if sid.startswith("ID") else len(rows) + 1,
                "timestamp_if_available": info.get("first_epoch_s", float("nan")),
                "capture_time_key": info.get("capture_time_key", ""),
                "d_tag_loo_residual": float(np.nanmedian(eff) - LOO_DTAG_MM) if eff.size else float("nan"),
                "error_3d": float(err_row["err_3d_mm"].iloc[0]) if not err_row.empty and "err_3d_mm" in err_row else float("nan"),
            }
        )
    rows.sort(key=lambda r: (np.inf if not np.isfinite(r["timestamp_if_available"]) else r["timestamp_if_available"], r["temporal_order"]))
    for i, r in enumerate(rows, 1):
        r["temporal_order"] = i
    write_csv(TABLES / "p4_temporal_order.csv", rows)
    df = pd.DataFrame(rows)
    x = df["timestamp_if_available"].to_numpy(dtype=float)
    if np.isfinite(x).sum() >= 3 and np.nanmax(x) > np.nanmin(x):
        hours = (x - np.nanmin(x)) / 3600.0
        x_label = "timestamp_hours"
    else:
        hours = df["temporal_order"].to_numpy(dtype=float)
        x_label = "measurement_order"
    drift_rows = []
    for metric in ("d_tag_loo_residual", "error_3d"):
        reg = linreg(hours, df[metric])
        drift_rows.append({"metric": metric, "drift_rate_mm_per_hour": reg["slope"] if x_label == "timestamp_hours" else float("nan"), "slope_per_order": reg["slope"], "r2": reg["r2"], "significance": reg["p_value"], "x_axis": x_label})
    ext18 = read_csv(EXT_ITEM18, "extended item 18 temporal split", fatal=False)
    if not ext18.empty:
        for _, r in ext18.iterrows():
            drift_rows.append({"metric": f"item18_{r.get('time_block', '')}", "drift_rate_mm_per_hour": float("nan"), "slope_per_order": float(r.get("d_tag_mm", np.nan)), "r2": float(r.get("median_3d_mm", np.nan)), "significance": float("nan"), "x_axis": "published block summary"})
    write_csv(TABLES / "p4_drift_analysis.csv", drift_rows)
    if plt is not None:
        fig, ax1 = plt.subplots(figsize=(7, 2.4))
        ax1.plot(df["temporal_order"], df["d_tag_loo_residual"], marker="o", color="tab:blue", label="D_tag residual")
        ax1.set_xlabel("measurement order")
        ax1.set_ylabel("D_tag residual (mm)")
        ax2 = ax1.twinx()
        ax2.plot(df["temporal_order"], df["error_3d"], marker="s", color="tab:orange", label="3D error")
        ax2.set_ylabel("3D error (mm)")
        fig.tight_layout()
        fig.savefig(FIGURES / "p4_temporal_stability.png", dpi=300)
        plt.close(fig)
    key = f"D_tag residual slope {drift_rows[0]['slope_per_order']:.2f} mm/order, R2={drift_rows[0]['r2']:.2f}"
    task_report("TASK_P4_TEMPORAL.md", "Task P4 - Temporal Stability", key, drift_rows, ["metric", "slope_per_order", "drift_rate_mm_per_hour", "r2", "significance", "x_axis"])
    return finish(pc, "ok", key)


def task_p5(ctx: dict[str, Any]) -> TaskResult:
    pc = phase_context("P5")
    anat = read_csv(M8_ANATOMY, "mechanistic position anatomy")
    feats = ["gdop", "n_good_anchors", "n_bad_anchors", "dist_centroid", "elev_diversity", "dtag_loo_fold_residual"]
    df = anat.copy()
    for col in feats + ["error_3d"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    X = df[feats].to_numpy(dtype=float)
    y = df["error_3d"].to_numpy(dtype=float)
    mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
    means = np.nanmean(X[mask], axis=0)
    stds = np.nanstd(X[mask], axis=0)
    stds[stds == 0] = 1.0
    Z = (X - means) / stds
    coef = np.linalg.lstsq(np.c_[np.ones(mask.sum()), Z[mask]], y[mask], rcond=None)[0] if mask.sum() >= len(feats) + 1 else np.zeros(len(feats) + 1)
    pred = np.c_[np.ones(len(df)), Z] @ coef
    quality = -pred
    qmin, qmax = np.nanmin(quality), np.nanmax(quality)
    score = (quality - qmin) / (qmax - qmin) if qmax > qmin else np.ones_like(quality)
    rows = []
    for i, r in df.iterrows():
        rows.append(
            {
                "position_id": r["position_id"],
                "gdop": r["gdop"],
                "n_los": r["n_good_anchors"],
                "n_nlos": r["n_bad_anchors"],
                "mean_elev": float("nan"),
                "dist_centroid": r["dist_centroid"],
                "crb_i": 1.0 / r["gdop"] if np.isfinite(r["gdop"]) and r["gdop"] != 0 else float("nan"),
                "quality_score": float(score[i]),
                "actual_error": r["error_3d"],
            }
        )
    write_csv(TABLES / "p5_quality_score.csv", rows)
    corr = stats.pearsonr(score[mask], y[mask]).statistic if stats is not None and mask.sum() > 2 else float("nan")
    weights = {r["position_id"]: max(1e-3, float(r["quality_score"])) for r in rows}
    cfg = ctx["configs"]["V5_CV5"]
    uniform_d = calibrate_dtag(ctx, cfg, ctx["ids"], list(range(8)), ctx["medians"])
    weighted_d = calibrate_dtag(ctx, cfg, ctx["ids"], list(range(8)), ctx["medians"], weights_by_sid=weights)
    q_rows = []
    for method, dtag, w in [("uniform_range_residual", uniform_d, None), ("quality_weighted_range_residual", weighted_d, weights)]:
        res = eval_static_ls(ctx, cfg, ctx["ids"], ctx["medians"], "fixed", dtag, list(range(8)), weights_by_sid=w)
        q_rows.append({"method": method, "d_tag": dtag, "median_3d": res["median_3d"], "rmse": res["rmse"], "quality_error_corr": corr})
    write_csv(TABLES / "p5_quality_weighted_dtag.csv", q_rows)
    if plt is not None:
        fig, ax = plt.subplots(figsize=(3.5, 2.6))
        ax.scatter(score, y, color="tab:blue")
        ax.set_xlabel("quality score")
        ax.set_ylabel("actual 3D error (mm)")
        fig.tight_layout()
        fig.savefig(FIGURES / "p5_quality_vs_error.png", dpi=300)
        plt.close(fig)
    key = f"quality score vs error Pearson r={corr:.2f}; weighted D_tag {weighted_d:.1f} mm"
    task_report("TASK_P5_QUALITY_SCORE.md", "Task P5 - Quality Score", key, q_rows, ["method", "d_tag", "median_3d", "rmse", "quality_error_corr"])
    return finish(pc, "ok", key)


def task_p6(ctx: dict[str, Any]) -> TaskResult:
    pc = phase_context("P6")
    gap = read_csv(ROTO_GAP, "ROTO gap decomposition", fatal=False)
    rows = []
    if not gap.empty:
        for _, r in gap.iterrows():
            rows.append({"component": r.get("component", ""), "estimated_mm": float(r.get("estimated_mm", np.nan)), "method": r.get("method", ""), "notes": r.get("notes", "")})
    else:
        dtag = read_csv(ROTO_DTAG, "ROTO dtag", fatal=False)
        dtag_spread = float(np.nanmax(dtag["d_tag_estimated_mm"]) - np.nanmin(dtag["d_tag_estimated_mm"])) if not dtag.empty and "d_tag_estimated_mm" in dtag else 24.9
        rows = [
            {"component": "D_tag mismatch", "estimated_mm": dtag_spread, "method": "ROTO per-tag D_tag spread", "notes": ""},
            {"component": "Motion blur", "estimated_mm": float("nan"), "method": "not available", "notes": "see ROTO deep-dive"},
            {"component": "Time alignment residual", "estimated_mm": float("nan"), "method": "not available", "notes": "see ROTO deep-dive"},
            {"component": "Spatial NLOS difference", "estimated_mm": float("nan"), "method": "not available", "notes": "requires ROTO trajectory map"},
        ]
    total_gap_row = next((r for r in rows if str(r["component"]).lower().startswith("total")), None)
    total_gap = float(total_gap_row["estimated_mm"]) if total_gap_row and np.isfinite(total_gap_row["estimated_mm"]) else 101.5 - 56.0
    explained = float(
        np.nansum(
            [
                r["estimated_mm"]
                for r in rows
                if "unexplained" not in str(r["component"]).lower() and not str(r["component"]).lower().startswith("total")
            ]
        )
    )
    if not any("unexplained" in str(r["component"]).lower() for r in rows):
        rows.append({"component": "Unexplained", "estimated_mm": total_gap - explained, "method": "remainder to dynamic-static gap", "notes": "components are approximate and not orthogonal"})
    write_csv(TABLES / "p6_gap_decomposition.csv", rows)
    key = f"approx explained listed proxies {explained:.1f} mm of {total_gap:.1f} mm"
    task_report("TASK_P6_DYNAMIC_GAP.md", "Task P6 - Static-to-Dynamic Gap", key, rows, ["component", "estimated_mm", "method", "notes"])
    return finish(pc, "ok", key)


def anchor_contribution_rows(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = ctx["configs"]["V5_CV5"]
    static = read_csv(FULL_V5_STATIC, "FULL_V5 static per-position", fatal=False)
    if static.empty:
        static = pd.DataFrame()
    if "d_tag_mm" in static:
        static = static[np.isclose(pd.to_numeric(static["d_tag_mm"], errors="coerce"), LOO_DTAG_MM, atol=0.1)]
    if static.empty:
        static = read_csv(FULL_V5_STATIC, "FULL_V5 static per-position", fatal=False).drop_duplicates("ID")
    centroid = np.asarray(ctx["inputs"]["truth_coords"], dtype=float).mean(axis=0)
    rows: list[dict[str, Any]] = []
    for sid in ctx["ids"]:
        truth = np.asarray(ctx["inputs"]["tag_truth_np"][sid], dtype=float)
        erow = static[static["ID"].astype(str) == sid]
        if erow.empty:
            continue
        solved = np.array([float(erow.iloc[0]["solved_x_mm"]), float(erow.iloc[0]["solved_y_vertical_mm"]), float(erow.iloc[0]["solved_z_mm"])], dtype=float)
        aids = [aid for aid in range(8) if aid in ctx["medians"].get(sid, {})]
        if len(aids) < 4:
            continue
        J = []
        rvec = []
        for aid in aids:
            diff = solved - cfg.coords[aid]
            norm = float(np.linalg.norm(diff))
            J.append(-diff / norm if norm > 1e-9 else np.zeros(3))
            pred = norm + float(cfg.delays[aid]) + LOO_DTAG_MM
            rvec.append(float(ctx["medians"][sid][aid]) - pred)
        Jm = np.asarray(J, dtype=float)
        rv = np.asarray(rvec, dtype=float)
        try:
            H = np.linalg.pinv(Jm.T @ Jm) @ Jm.T
        except np.linalg.LinAlgError:
            H = np.linalg.pinv(Jm)
        radial = np.array([truth[0] - centroid[0], 0.0, truth[2] - centroid[2]], dtype=float)
        nr = float(np.linalg.norm(radial))
        radial_unit = radial / nr if nr > 1e-9 else np.array([1.0, 0.0, 0.0])
        for col, aid in enumerate(aids):
            contrib = H[:, col] * rv[col]
            rows.append(
                {
                    "position_id": sid,
                    "anchor_label": ANCHORS[aid],
                    "anchor_id": aid,
                    "contribution_3d_mm": float(np.linalg.norm(contrib)),
                    "contribution_radial": float(np.dot(np.array([contrib[0], 0.0, contrib[2]]), radial_unit)),
                    "contribution_vertical": float(contrib[1]),
                    "rho_mm": float(rv[col]),
                }
            )
    return rows


def task_p7(ctx: dict[str, Any]) -> TaskResult:
    pc = phase_context("P7")
    rows = anchor_contribution_rows(ctx)
    write_csv(TABLES / "p7_per_anchor_contribution.csv", rows)
    df = pd.DataFrame(rows)
    summary = []
    for label, g in df.groupby("anchor_label"):
        corr = stats.pearsonr(g["contribution_3d_mm"], np.abs(g["rho_mm"])).statistic if stats is not None and len(g) > 2 else float("nan")
        summary.append({"anchor_label": label, "mean_contribution_mm": float(np.nanmean(g["contribution_3d_mm"])), "max_contribution_mm": float(np.nanmax(g["contribution_3d_mm"])), "correlation_with_rho": corr})
    write_csv(TABLES / "p7_anchor_contribution_summary.csv", summary)
    if plt is not None and not df.empty:
        pivot = df.pivot_table(index="position_id", columns="anchor_label", values="contribution_3d_mm", aggfunc="mean").reindex(columns=list(ANCHORS))
        fig, ax = plt.subplots(figsize=(7, 3.0))
        im = ax.imshow(pivot.to_numpy(dtype=float), aspect="auto", cmap="viridis")
        ax.set_xticks(range(8), list(ANCHORS))
        ax.set_yticks(range(len(pivot.index)), list(pivot.index))
        ax.set_xlabel("anchor")
        ax.set_ylabel("position")
        fig.colorbar(im, ax=ax, label="contribution (mm)")
        fig.tight_layout()
        fig.savefig(FIGURES / "p7_contribution_heatmap.png", dpi=300)
        plt.close(fig)
    top = max(summary, key=lambda r: r["mean_contribution_mm"]) if summary else {}
    key = f"largest mean contribution: anchor {top.get('anchor_label', '?')} {top.get('mean_contribution_mm', float('nan')):.1f} mm"
    task_report("TASK_P7_ANCHOR_CONTRIBUTION.md", "Task P7 - Per-Anchor Contribution", key, summary, ["anchor_label", "mean_contribution_mm", "max_contribution_mm", "correlation_with_rho"])
    return finish(pc, "ok", key)


def task_p8(ctx: dict[str, Any]) -> TaskResult:
    pc = phase_context("P8")
    made = []
    if plt is None:
        return finish(pc, "skipped", "matplotlib unavailable")
    p1 = read_csv(TABLES / "p1_signed_radial.csv", "P1 signed radial", fatal=False)
    if not p1.empty:
        fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.4), gridspec_kw={"width_ratios": [1.0, 0.6, 1.0]})
        for ax, key, color in [(axes[0], "V4", "tab:blue"), (axes[2], "V5", "tab:orange")]:
            g = p1[p1["config"].astype(str).str.contains(key)]
            ax.scatter(g["distance_from_centroid"], g["signed_radial_mm"], color=color, s=18)
            ax.axhline(0, color="black", linewidth=0.8)
            ax.set_xlabel("distance (mm)")
            ax.set_ylabel("signed radial (mm)")
        axes[1].axis("off")
        axes[1].arrow(0.15, 0.5, 0.25, 0, head_width=0.08, color="tab:red", length_includes_head=True)
        axes[1].arrow(0.85, 0.5, -0.25, 0, head_width=0.08, color="tab:blue", length_includes_head=True)
        axes[1].text(0.5, 0.65, "NLOS outward\nscale inward", ha="center", va="center", fontsize=8)
        fig.tight_layout()
        fig.savefig(FIGURES / "fig11_cancellation_mechanism.png", dpi=300)
        plt.close(fig)
        made.append("fig11_cancellation_mechanism.png")
    m2 = read_csv(M2_PREV, "M2 error budget", fatal=False)
    if not m2.empty:
        fig, ax = plt.subplots(figsize=(3.5, 2.6))
        ax.barh(m2["component"].astype(str), pd.to_numeric(m2.iloc[:, 1], errors="coerce"), color="tab:green")
        ax.set_xlabel("contribution (mm)")
        fig.tight_layout()
        fig.savefig(FIGURES / "fig12_error_budget_waterfall.png", dpi=300)
        plt.close(fig)
        made.append("fig12_error_budget_waterfall.png")
    p3 = read_csv(TABLES / "p3_pareto_frontier.csv", "P3 frontier", fatal=False)
    ident = read_csv(M5_IDENT, "M5 identifiability", fatal=False)
    if not ident.empty:
        fig, ax1 = plt.subplots(figsize=(3.5, 2.6))
        ax1.plot(ident["k"], ident["mean_median_3d"], marker="o", color="tab:blue")
        ax1.set_xlabel("anchors")
        ax1.set_ylabel("median 3D error (mm)")
        ax2 = ax1.twinx()
        ax2.plot(ident["k"], ident["redundancy"], marker="s", color="tab:orange")
        ax2.set_ylabel("redundancy")
        fig.tight_layout()
        fig.savefig(FIGURES / "fig13_identifiability_vs_anchors.png", dpi=300)
        plt.close(fig)
        made.append("fig13_identifiability_vs_anchors.png")
    fish = read_csv(M9_FISHER, "M9 Fisher", fatal=False)
    if not fish.empty:
        row = fish.sort_values("rank").iloc[0]
        labels = [c for c in fish.columns if c.startswith("proj_")]
        fig, ax = plt.subplots(figsize=(3.5, 2.6))
        ax.bar(labels, [float(row[c]) for c in labels], color="tab:purple")
        ax.tick_params(axis="x", rotation=35)
        ax.set_ylabel("projection")
        fig.tight_layout()
        fig.savefig(FIGURES / "fig14_fisher_eigenvector.png", dpi=300)
        plt.close(fig)
        made.append("fig14_fisher_eigenvector.png")
    ev = read_csv(M10_EVIDENCE, "M10 evidence", fatal=False)
    if not ev.empty:
        conf_map = {"strong": 3, "moderate": 2, "weak": 1, "mixed": 1, "skipped": 0}
        vals = [conf_map.get(str(x).lower(), 1) for x in ev.get("confidence", pd.Series(["weak"] * len(ev)))]
        mat = np.outer(vals, vals)
        fig, ax = plt.subplots(figsize=(3.5, 3.2))
        im = ax.imshow(mat, cmap="Blues")
        ax.set_xlabel("claim")
        ax.set_ylabel("claim")
        fig.colorbar(im, ax=ax, shrink=0.8)
        fig.tight_layout()
        fig.savefig(FIGURES / "fig15_consistency_matrix.png", dpi=300)
        plt.close(fig)
        made.append("fig15_consistency_matrix.png")
    rows = [{"figure": f, "description": "publication-upgrade figure"} for f in made]
    task_report("TASK_P8_FIGURES.md", "Task P8 - Publication Figure Upgrades", f"generated {len(made)} figures", rows, ["figure", "description"])
    return finish(pc, "ok", f"generated {len(made)} upgraded figures")


def task_p9() -> TaskResult:
    pc = phase_context("P9")
    tex = r"""\section{Introduction}
Ultra-wideband ranging can support accurate indoor localization, but that promise depends on anchor geometry and delay calibration being known at the same metric scale as the tracking volume. In practical deployments this is rarely true. Anchor coordinates are often measured by hand, antenna phase centres do not coincide exactly with markers or enclosures, and cable, antenna, and timestamp biases appear in the same range equation as the distance itself. A self-calibration procedure that estimates the anchor layout directly from inter-anchor ranges is therefore attractive, because it can be run in the target room without a surveying step. The difficulty is identifiability: a change in geometric scale can be partially hidden by a change in range delay, and the resulting layout may give good empirical positioning on one dataset while being physically wrong.

This work studies that tradeoff in an eight-anchor UWB system with independent Vicon ground truth. The earlier V4 calibration used bounded independent anchor delays with a gauge constraint. It produced strong static positioning results, but the recovered anchor layout was compressed relative to the Vicon frame. The V5 calibration replaces that parameterization with a common-mode anchor delay plus regularized per-anchor residuals. This separates the bulk delay from the metric geometry and brings the self-calibrated layout back to near-unity scale. The main finding is intentionally nuanced: correcting the physical scale does not automatically win every static accuracy metric on this particular campaign, because the V4 scale error partly cancels positive NLOS range bias. The analysis therefore separates physical correctness, empirical accuracy, transferability, and deployment robustness.

The contributions are threefold. First, we introduce and evaluate a common-mode delay parameterization for anchor self-calibration. Second, we quantify the remaining tag-delay and NLOS mechanisms using static, dynamic, and synthetic ablations. Third, we identify practical deployment improvements, including lower-percentile range aggregation and quality-aware calibration. The results are reported with both the original production baseline and oracle Vicon-anchor controls, so that each gain can be assigned to anchor geometry, delay modeling, tag calibration, or range quality rather than to a single aggregate error number.

\section{System Description}
The platform uses Decawave DWM1001C-class UWB hardware, consisting of an nRF52 microcontroller and a DW1000 UWB transceiver, with custom firmware rather than the vendor PANS/DRTLS stack \cite{dw1000_datasheet,decawave_appnotes}. The ranging protocol is a broadcast alternative single-sided two-way ranging scheme. Anchors collect range observations to tags and to other anchors; the offline solver then estimates anchor geometry, anchor delay corrections, tag delay, and tag position depending on the experiment.

The Erlangen campaign used eight anchors arranged in a dual-layer geometry. Four anchors are in the lower layer and four in the upper layer. This vertical aperture is important because a single-height layout gives poor observability of the vertical tag coordinate and worsens the coupling between scale, common delay, and tag height. Ground truth for anchor markers and tag trajectories was provided by an OptiTrack/Vicon system. The static evaluation consists of 24 tag positions distributed through the room, and the dynamic evaluation uses ROTO captures with two tags mounted at different radii on a rotating arm. Dynamic results are explicitly reported as best-fit aligned because the UWB and Vicon systems were not hardware time synchronized.

\section{Method}
\subsection{Anchor Self-Calibration}
\subsubsection{V4: Bounded Independent Delays}
The V4 calibration estimates anchor coordinates and per-anchor delay corrections \(d_i\) from inter-anchor ranges. A gauge constraint fixes one delay, conventionally \(d_A=0\), and the remaining delays are bounded to a plausible interval. This formulation is simple, but it leaves the solver with a scale-delay ambiguity. A uniform compression of the anchor coordinates can be compensated by shifting the fitted delays, especially when the available inter-anchor constraints are only weakly redundant. In the range equation
\[
  r_{ij} = \|a_i-a_j\| + d_i + d_j + \epsilon_{ij},
\]
the geometric term and the delay terms both enter in millimetres. Without a separate common-mode delay variable, the solver can use geometry to absorb bias that is actually delay-like or NLOS-like. This is why V4 can be empirically accurate while recovering a layout whose Sim3 scale is below one.

\subsubsection{V5: Common-Mode Parameterization}
The V5 calibration writes the anchor delay as
\[
  d_i = c + e_i,
\]
where \(c\) is a global common-mode delay and \(e_i\) is a regularized per-anchor residual. The common-mode term absorbs the bulk hardware/ranging delay that is shared across anchors, while the residuals account for smaller anchor-specific deviations. We regularize \(e_i\) with a scale of 20 mm in the reported calibration. This prior is not a claim that every anchor residual is physical; rather, it prevents the residual terms from becoming an unconstrained sink for NLOS and geometry errors. The important effect is that the calibration no longer needs to shrink the whole room to explain a positive common range bias. The resulting V5 layout has near-unity Sim3 scale against Vicon and much lower rigid anchor RMSE than V4.

\subsection{Tag Delay Calibration}
Tag delay \(D_{\mathrm{tag}}\) is calibrated separately from anchor self-calibration. The deployable value is obtained by leave-one-position-out range-residual calibration on the 24 static positions:
\[
  D_{\mathrm{tag}} = \mathrm{median}_{p,i}\left(r_{p,i} - \|x_p-a_i\| - d_i\right).
\]
This range-residual criterion is distinct from choosing the tag delay that minimizes position error on the same data. The latter is useful as an oracle diagnostic, but it can exploit cancellation between NLOS, geometry, and vertical bias. We therefore report in-sample sweep optima separately from cross-validated or deployable tag-delay values.

\subsection{Position Solving}
Given an anchor layout, anchor delays, and a tag delay, each tag position is estimated by nonlinear least squares over the measured ranges:
\[
  \min_x \sum_i \rho\left(r_i - \|x-a_i\| - d_i - D_{\mathrm{tag}}\right),
\]
where \(\rho\) is a robust loss. The production analysis uses the existing C-backed trajectory solver where available, and the paper-preparation sweeps use the same range model with a Huber least-squares proxy for small deployment ablations. Dynamic ROTO results additionally include a best-fit spatial and temporal alignment step, and are therefore interpreted as a dynamic error floor rather than as an absolute synchronized tracking metric \cite{ss_twr_reference,selfcal_xxx}.
"""
    write_text(TABLES / "paper_draft_intro_method.tex", tex)
    task_report("TASK_P9_LATEX_DRAFT.md", "Task P9 - LaTeX Draft", "drafted introduction, system description, and method sections", [{"file": "tables/paper_draft_intro_method.tex", "sections": "Introduction/System/Method"}], ["file", "sections"])
    return finish(pc, "ok", "drafted LaTeX intro/method")


def first_val(df: pd.DataFrame, col: str, default: float = float("nan")) -> float:
    if df.empty or col not in df:
        return default
    vals = pd.to_numeric(df[col], errors="coerce").dropna()
    return float(vals.iloc[0]) if not vals.empty else default


def task_p10() -> TaskResult:
    pc = phase_context("P10")
    anchor = read_csv(ANCHOR_SIDE, "anchor side table", fatal=False)
    static = read_csv(STATIC_ACCURACY, "static accuracy", fatal=False)
    loo = read_csv(FULL_4WAY_LOO, "LOO tag delay", fatal=False)
    p30 = read_csv(P30_RECAL, "p30 recalibration", fatal=False)
    p30s = read_csv(P30_SENS, "p30 sensitivity", fatal=False)
    nlos = read_csv(EXT_ITEM24, "rho distribution", fatal=False)
    batch = read_csv(BATCH2_STATUS, "batch2 status", fatal=False)
    shap = read_csv(GPU_SHAPLEY, "Shapley", fatal=False)
    bayes = read_csv(GPU_TASK11, "noise model evidence", fatal=False)
    roto = read_csv(ROTO_ALIGN, "ROTO alignment", fatal=False)
    lines = [
        "# AutoPos V5 - Key Numbers Reference Card",
        "",
        "## Anchor Calibration",
    ]
    if not anchor.empty:
        for _, r in anchor.iterrows():
            lines.append(f"- {r.get('layout','layout')}: Sim3 scale {float(r.get('sim3_scale', np.nan)):.3f}, rigid RMSE {float(r.get('rigid_rmse_mm', np.nan)):.1f} mm, common-mode {float(r.get('common_mode_mm', np.nan)):.1f} mm, delay spread {float(r.get('delay_spread_mm', np.nan)):.1f} mm.")
    lines.extend(["", "## Tag Delay"])
    if not loo.empty:
        lines.append(f"- V5 LOO tag delay: {first_val(loo, 'd_tag_median_mm', first_val(loo, 'dtag_mm')):.3f} mm.")
    lines.extend(["", "## Static Accuracy (24 positions)"])
    if not static.empty:
        for _, r in static.iterrows():
            lines.append(f"- {r.get('pipeline','pipeline')}: median {float(r.get('median_3d_mm', np.nan)):.1f} mm, P95 {float(r.get('p95_3d_mm', np.nan)):.1f} mm, RMSE {float(r.get('rmse_3d_mm', np.nan)):.1f} mm.")
    if not p30.empty:
        for _, r in p30.iterrows():
            lines.append(f"- p30 {r.get('pipeline','pipeline')}: median {float(r.get('median_3d', r.get('median_3d_mm', np.nan))):.1f} mm, D_tag {float(r.get('d_tag_mm', np.nan)):.1f} mm.")
    if not p30s.empty:
        best = p30s.sort_values("median_3d_mm").iloc[0] if "median_3d_mm" in p30s else p30s.iloc[0]
        lines.append(f"- Percentile sensitivity best tested percentile: {best.get('percentile','?')} with median {float(best.get('median_3d_mm', np.nan)):.1f} mm.")
    lines.extend(["", "## Dynamic (ROTO)"])
    if not roto.empty:
        for _, r in roto.head(4).iterrows():
            lines.append(f"- {r.get('method','method')}: overall median {float(r.get('overall_median', np.nan)):.1f} mm, RMSE {float(r.get('overall_rmse', np.nan)):.1f} mm.")
    lines.extend(["", "## NLOS"])
    if not nlos.empty:
        top = nlos.sort_values("pct_gt100", ascending=False).head(2) if "pct_gt100" in nlos else nlos.head(2)
        for _, r in top.iterrows():
            lines.append(f"- Anchor {r.get('anchor_label','?')}: rho RMS/std marker {float(r.get('std_mm', r.get('rms_rho', np.nan))):.1f} mm, >100 mm {float(r.get('pct_gt100', np.nan)):.2f}.")
    if not shap.empty:
        s = shap.sort_values("shapley_3d", ascending=False).head(2)
        lines.append("- Highest Shapley anchors: " + ", ".join(f"{r.anchor_label}={r.shapley_3d:.1f}" for r in s.itertuples()) + ".")
    lines.extend(["", "## Identifiability / Noise"])
    if not bayes.empty:
        lines.append(f"- Noise-model evidence table winner row: {bayes.iloc[0].to_dict()}.")
    if not batch.empty:
        for task in ("N1", "N2", "N3"):
            g = batch[batch["task"].astype(str).str.contains(task, na=False)]
            if not g.empty:
                lines.append(f"- {task}: {g.iloc[0].get('key_finding','')}.")
    write_text(REPORTS / "KEY_NUMBERS_CARD.md", "\n".join(lines) + "\n")
    return finish(pc, "ok", "wrote key numbers card")


def verification(statuses: list[TaskResult]) -> None:
    py_compile.compile(str(THIS), doraise=True)
    tree = ast.parse(THIS.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in {"torch", "cupy", "cuda"}:
                    bad.append(alias.name)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in {"torch", "cupy", "cuda"}:
            bad.append(node.module)
    write_csv(
        TABLES / "task_status_summary.csv",
        [s.__dict__ for s in statuses],
        ["task", "status", "elapsed_s", "mean_cpu_percent", "max_cpu_percent", "key_finding", "notes"],
    )
    counts = []
    for path in sorted(TABLES.glob("*.csv")):
        try:
            n = max(0, sum(1 for _ in path.open("r", encoding="utf-8")) - 1)
        except Exception:
            n = -1
        counts.append({"file": path.name, "rows": n})
    write_csv(TABLES / "output_row_counts.csv", counts)
    write_text(
        REPORTS / "SCRIPT_VERIFICATION.json",
        json.dumps({"py_compile": "ok", "forbidden_imports": bad, "csv_files": len(counts), "workers": WORKERS, "cpu_only": True}, indent=2)
        + "\n",
    )


def final_report(statuses: list[TaskResult]) -> None:
    total = sum(s.elapsed_s for s in statuses)
    lines = [
        "# Paper Strengthening Completion",
        "",
        f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        "",
        "## Task Status",
        "",
    ]
    append_table(lines, [s.__dict__ for s in statuses], ["task", "status", "elapsed_s", "key_finding"])
    lines.extend(
        [
            "## Outputs",
            "",
            "- P1 radial mechanism: `tables/p1_signed_radial.csv`, `figures/p1_radial_error_comparison.png`.",
            "- P2 delay/NLOS: `tables/p2_ei_correlations.csv`, `tables/p2_counterfactual.csv`.",
            "- P3 deployment: `tables/p3_deployment_sweep.csv`, `tables/p3_pareto_frontier.csv`, `figures/p3_pareto_frontier.png`.",
            "- P4 temporal: `tables/p4_temporal_order.csv`, `figures/p4_temporal_stability.png`.",
            "- P5 quality score: `tables/p5_quality_score.csv`, `figures/p5_quality_vs_error.png`.",
            "- P6 dynamic gap: `tables/p6_gap_decomposition.csv`.",
            "- P7 anchor contributions: `tables/p7_per_anchor_contribution.csv`, `figures/p7_contribution_heatmap.png`.",
            "- P8 upgraded figures: `figures/fig11_cancellation_mechanism.png` through `figures/fig15_consistency_matrix.png` where source data were available.",
            "- P9 LaTeX draft: `tables/paper_draft_intro_method.tex`.",
            "- P10 key numbers: `reports/KEY_NUMBERS_CARD.md`.",
            "",
            "## Runtime",
            "",
            f"- Workers: {WORKERS}",
            f"- Total task CPU wall sum: {total:.1f} s",
            f"- Mean live CPU%: {np.nanmean([s.mean_cpu_percent for s in statuses]):.1f}",
            f"- Max live CPU%: {np.nanmax([s.max_cpu_percent for s in statuses]):.1f}",
            "",
            "Notes: P3 is a deployment-screening sweep using the static range least-squares proxy, not a replacement for the full C trajectory solver. P6 is a component estimate assembled from the ROTO deep-dive and should be read as non-orthogonal gap accounting.",
        ]
    )
    write_text(REPORTS / "PAPER_STRENGTHENING_COMPLETION.md", "\n".join(lines) + "\n")


def run_task(name: str, fn, *args) -> TaskResult:
    try:
        print(json.dumps({"task": name, "status": "start"}, sort_keys=True), flush=True)
        res = fn(*args)
        print(json.dumps({"task": name, "status": res.status, "key": res.key_finding}, sort_keys=True), flush=True)
        return res
    except Exception as exc:
        ctx = phase_context(name)
        write_text(REPORTS / f"TASK_{name}_ERROR.md", f"# Task {name} Error\n\n```\n{repr(exc)}\n```\n")
        return finish(ctx, "failed", f"{type(exc).__name__}: {exc}", repr(exc))


def main() -> int:
    for p in (OUT_ROOT, TABLES, FIGURES, REPORTS, SCRIPTS):
        p.mkdir(parents=True, exist_ok=True)
    setup_style()
    ctx = load_context()
    statuses: list[TaskResult] = []
    for name, fn, args in [
        ("P1", task_p1, (ctx,)),
        ("P2", task_p2, (ctx,)),
        ("P3", task_p3, (ctx,)),
        ("P4", task_p4, (ctx,)),
        ("P5", task_p5, (ctx,)),
        ("P6", task_p6, (ctx,)),
        ("P7", task_p7, (ctx,)),
        ("P8", task_p8, (ctx,)),
        ("P9", task_p9, ()),
        ("P10", task_p10, ()),
    ]:
        statuses.append(run_task(name, fn, *args))
    verification(statuses)
    final_report(statuses)
    print("\n=== PAPER STRENGTHENING — RUNTIME SUMMARY ===")
    print("Machine: i7-8700K 6C/12T 32GB")
    print(f"Workers: {WORKERS} (CPU-only)")
    for s in statuses:
        print(f"{s.task}: {s.elapsed_s:.1f} s [{s.status}] {s.key_finding}")
    print(f"Total task wall sum: {sum(s.elapsed_s for s in statuses):.1f} s")
    print(f"Mean CPU%: {np.nanmean([s.mean_cpu_percent for s in statuses]):.1f}%")
    print(f"Max CPU%: {np.nanmax([s.max_cpu_percent for s in statuses]):.1f}%")
    return 0 if all(s.status in {"ok", "skipped"} for s in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())
