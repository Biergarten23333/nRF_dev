#!/usr/bin/env python3
from __future__ import annotations

import csv
import ast
import importlib.util
import json
import math
import multiprocessing as mp
import os
import py_compile
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

import numpy as np
import pandas as pd
import psutil

try:
    from scipy import linalg, optimize, stats
except Exception:  # pragma: no cover
    linalg = None
    optimize = None
    stats = None

THIS = Path(__file__).resolve()
ANALYSIS = THIS.parents[2]
BASE = THIS.parents[4]
OUT_ROOT = ANALYSIS / "FULL_V5_mechanistic_deepdive"
TABLES = OUT_ROOT / "tables"
FIGURES = OUT_ROOT / "figures"
REPORTS = OUT_ROOT / "reports"
SCRIPTS = OUT_ROOT / "scripts"

EXT_SCRIPT = ANALYSIS / "FULL_V5_extended_mechanism_ablations/scripts/run_extended_mechanism_ablations.py"
FULL_V5_SCRIPT = ANALYSIS / "FULL_V5/scripts/run_full_v5_ablation_pipeline.py"
TRANSFER_CELLS = ANALYSIS / "FULL_transfer_matrix/tables/transfer_matrix_48cells.csv"
TRANSFER_SWEEP = ANALYSIS / "FULL_transfer_matrix/tables/transfer_matrix_Dsweep_detail.csv"
FULL_V5_STATIC = ANALYSIS / "FULL_V5/tables/static_per_position.csv"
FULL_V5_DOP = ANALYSIS / "FULL_V5/tables/dop_per_position.csv"
FULL_V5_STATIC_RHO = ANALYSIS / "FULL_V5/tables/per_anchor_residual_static.csv"
FULL_V5_DELAY_COMP = ANALYSIS / "FULL_V5/tables/delay_comparison_v4_vs_v5.csv"
VICON_CM_DELAY = ANALYSIS / "FULL_V5_align_to_Vicon/tables/vicon_anchor_delays_refit_cm.csv"
V5_SCALE_COMP = ANALYSIS / "FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv"
GPU_SHAPLEY = ANALYSIS / "FULL_V5_GPU_discovery/tables/task3_shapley_values.csv"
GPU_CANDIDATES = ANALYSIS / "FULL_V5_GPU_discovery/tables/task17_candidate_info_gain.csv"
GPU_MC = ANALYSIS / "FULL_V5_overnight_batch2/tables/n1_adversarial_rooms.csv"
GPU_FISHER = ANALYSIS / "FULL_V5_GPU_discovery/tables/task2_fisher_joint.csv"
GPU_TASK4 = ANALYSIS / "FULL_V5_GPU_discovery/tables/task4_asymmetry_summary.csv"
GPU_TASK6 = ANALYSIS / "FULL_V5_GPU_discovery/tables/task6_cv_results.csv"
GPU_TASK11 = ANALYSIS / "FULL_V5_GPU_discovery/tables/task11_model_evidence.csv"
FOLLOWUP_P30 = ANALYSIS / "FULL_V5_followup_validation/tables/f1_p30_static_results.csv"
FOLLOWUP_VERIFICATION = ANALYSIS / "FULL_V5_followup_validation/tables/verification.csv"
OVERNIGHT_N4 = ANALYSIS / "FULL_V5_overnight_batch2/tables/n4_p30_recalibration.csv"
ROTO_DEEP = ANALYSIS / "FULL_V5_roto_deepdive"
ROTO_R6 = ROTO_DEEP / "tables/r6_phase_aggregate.csv"
ROTO_R3_SUMMARY = ROTO_DEEP / "tables/r3_joint_summary.csv"
ROTO_R3_DTAG = ROTO_DEEP / "tables/r3_estimated_dtag.csv"
ROTO_SAMPLES = ROTO_DEEP / "tables/roto_v5_dloo_samples.csv"
ROTO_RANGES = ROTO_DEEP / "tables/roto_v5_dloo_ranges_long.csv"

WORKERS = 6
LOO_DTAG_MM = 49.621
V5_COMMON_MODE_MM = 111.985
STATIC_BEST_MM = 56.0
ANCHORS = tuple("ABCDEFGH")
LOWER = {0, 1, 2, 3}
UPPER = {4, 5, 6, 7}


@dataclass(frozen=True)
class TaskResult:
    task: str
    status: str
    elapsed_s: float
    mean_cpu_percent: float
    max_cpu_percent: float
    key_finding: str
    notes: str = ""


def require_path(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"missing required input for {label}: {path}")
    return path


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
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_md_table(lines: list[str], rows: list[dict[str, Any]], cols: list[str], max_rows: int | None = None) -> None:
    lines.append("| " + " | ".join(cols) + " |\n")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |\n")
    shown = rows if max_rows is None else rows[:max_rows]
    for row in shown:
        vals = []
        for col in cols:
            val = row.get(col, "")
            if isinstance(val, (float, np.floating)):
                vals.append("" if not np.isfinite(float(val)) else f"{float(val):.3f}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |\n")
    if max_rows is not None and len(rows) > max_rows:
        lines.append(f"\n... {len(rows) - max_rows} rows omitted ...\n")
    lines.append("\n")


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
        return {"beta": float("nan"), "intercept": float("nan"), "r2": float("nan"), "p_value": float("nan"), "n": int(xx.size)}
    if stats is not None:
        res = stats.linregress(xx, yy)
        return {"beta": float(res.slope), "intercept": float(res.intercept), "r2": float(res.rvalue * res.rvalue), "p_value": float(res.pvalue), "n": int(xx.size)}
    beta, intercept = np.polyfit(xx, yy, 1)
    pred = beta * xx + intercept
    ss_res = float(np.sum((yy - pred) ** 2))
    ss_tot = float(np.sum((yy - np.mean(yy)) ** 2))
    return {"beta": float(beta), "intercept": float(intercept), "r2": float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan"), "p_value": float("nan"), "n": int(xx.size)}


def phase_context(task: str) -> dict[str, Any]:
    psutil.cpu_percent(interval=None)
    return {
        "task": task,
        "start": time.perf_counter(),
        "cpu": [],
        "physical_cores": psutil.cpu_count(logical=False) or 0,
        "logical_cores": psutil.cpu_count(logical=True) or os.cpu_count() or 0,
        "workers": WORKERS,
    }


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


def make_dirs() -> None:
    for p in (OUT_ROOT, TABLES, FIGURES, REPORTS, SCRIPTS):
        p.mkdir(parents=True, exist_ok=True)


def load_context() -> dict[str, Any]:
    ext = load_module(require_path(EXT_SCRIPT, "extended mechanism script"), "mechanistic_ext")
    mech, full = ext.load_previous_modules()
    inputs, configs, assignments, maps = ext.build_inputs_and_configs(mech, full)
    static_files = [Path(p) for p in inputs["static_files"]]
    raw_ranges, raw_info = ext.load_raw_ranges(static_files)
    if hasattr(ext, "raw_medians"):
        medians = ext.raw_medians(raw_ranges)
    else:
        medians = {sid: {aid: float(np.nanmedian(vals)) for aid, vals in by.items()} for sid, by in raw_ranges.items()}
    ids = sorted(inputs["tag_truth_np"])
    residuals = {
        key: ext.residual_observations(cfg, medians, inputs["tag_truth_np"], maps, LOO_DTAG_MM)
        for key, cfg in configs.items()
        if key in ("V4_CV4", "V5_CV5", "Vicon_Ccm", "V5_Cnone")
    }
    return {
        "ext": ext,
        "full": full,
        "inputs": inputs,
        "configs": configs,
        "assignments": assignments,
        "maps": maps,
        "raw_ranges": raw_ranges,
        "raw_info": raw_info,
        "medians": medians,
        "ids": ids,
        "residuals": residuals,
    }


def static_chunk_worker(chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[_name] = "1"
    ext = load_module(EXT_SCRIPT, f"mechanistic_ext_worker_{os.getpid()}")
    return [ext.solve_static_job_ext(job) for job in chunk]


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def run_static_jobs(jobs: list[dict[str, Any]], ctx: dict[str, Any], stage: str, chunk_size: int = 1) -> list[dict[str, Any]]:
    if not jobs:
        return []
    chunks = chunked(jobs, chunk_size)
    results: list[dict[str, Any]] = []
    done = 0
    mp_ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=WORKERS, mp_context=mp_ctx) as pool:
        futures = [pool.submit(static_chunk_worker, chunk) for chunk in chunks]
        for fut in as_completed(futures):
            part = fut.result()
            results.extend(part)
            done += len(part)
            sample_cpu(ctx)
            print(json.dumps({"stage": stage, "done": done, "total": len(jobs), "live_cpu_percent": ctx["cpu"][-1]}, sort_keys=True), flush=True)
    by_id = {r["job_id"]: r for r in results}
    return [by_id[j["job_id"]] for j in jobs]


def run_static_baselines(ctx_data: dict[str, Any]) -> dict[str, pd.DataFrame]:
    cache = TABLES / "baseline_static_position_rows.csv"
    if cache.exists():
        df = pd.read_csv(cache)
        return {cfg: g.copy() for cfg, g in df.groupby("mechanistic_config")}
    ext = ctx_data["ext"]
    inputs = ctx_data["inputs"]
    configs = ctx_data["configs"]
    ids = ctx_data["ids"]
    jobs = []
    for label in ("V4_CV4", "V5_CV5", "Vicon_Ccm"):
        jobs.append(ext.make_static_job(job_id=f"baseline_{label}", config=configs[label], d_tag_mm=LOO_DTAG_MM, ids=ids, inputs=inputs, return_rows=True, meta={"mechanistic_config": label}))
    job_ctx = phase_context("M0_static_baseline_replay")
    results = run_static_jobs(jobs, job_ctx, "mechanistic_static_baselines", chunk_size=1)
    rows = []
    for r in results:
        label = r["meta"]["mechanistic_config"]
        for row in r["rows"]:
            rows.append({"mechanistic_config": label, **row})
    write_csv(cache, rows)
    df = pd.DataFrame(rows)
    return {cfg: g.copy() for cfg, g in df.groupby("mechanistic_config")}


def solve_point_ls(
    measured: dict[int, float],
    coords: np.ndarray,
    delays: dict[int, float],
    dtag: float,
    anchor_ids: list[int],
    x0: np.ndarray,
) -> np.ndarray | None:
    if optimize is None or len(anchor_ids) < 4:
        return None
    aids = [int(a) for a in anchor_ids if int(a) in measured]
    if len(aids) < 4:
        return None

    def residual(x: np.ndarray) -> np.ndarray:
        return np.array([float(measured[aid]) - float(np.linalg.norm(x - coords[aid])) - float(delays[aid]) - float(dtag) for aid in aids], dtype=float)

    try:
        res = optimize.least_squares(residual, x0=np.asarray(x0, dtype=float), loss="huber", f_scale=100.0, max_nfev=200)
    except Exception:
        return None
    return np.asarray(res.x, dtype=float) if res.success else None


def calibrate_dtag_subset(ctx_data: dict[str, Any], cfg: Any, ids: list[str], anchor_ids: list[int]) -> float:
    vals = []
    for sid in ids:
        truth = np.asarray(ctx_data["inputs"]["tag_truth_np"][sid], dtype=float)
        for aid in anchor_ids:
            if aid in ctx_data["medians"].get(sid, {}):
                vals.append(float(ctx_data["medians"][sid][aid]) - float(np.linalg.norm(truth - cfg.coords[aid])) - float(cfg.delays[aid]))
    return float(np.nanmedian(vals)) if vals else float("nan")


def summarize_errors(errors: list[float]) -> dict[str, float]:
    return {"median_3d": pct(errors, 50), "p95_3d": pct(errors, 95), "rmse": rmse(errors)}


def task_m1(ctx_data: dict[str, Any], baselines: dict[str, pd.DataFrame]) -> tuple[TaskResult, list[dict[str, Any]]]:
    ctx = phase_context("M1")
    rows = []
    summary = []
    cfg_map = {"V4_CV4": "V4+C_V4+D_LOO", "V5_CV5": "V5+C_V5+D_LOO", "Vicon_Ccm": "Vicon+C_cm+D_LOO"}
    centroid = np.asarray(ctx_data["inputs"]["truth_coords"], dtype=float).mean(axis=0)
    for label, df in baselines.items():
        if label not in cfg_map:
            continue
        vals = []
        for _, r in df.iterrows():
            truth = np.array([r["truth_x_mm"], r["truth_y_vertical_mm"], r["truth_z_mm"]], dtype=float)
            solved = np.array([r["solved_x_mm"], r["solved_y_vertical_mm"], r["solved_z_mm"]], dtype=float)
            err = solved - truth
            radial_vec = np.array([truth[0] - centroid[0], 0.0, truth[2] - centroid[2]], dtype=float)
            nr = float(np.linalg.norm(radial_vec))
            radial_unit = radial_vec / nr if nr > 1e-9 else np.array([1.0, 0.0, 0.0])
            horiz_err = np.array([err[0], 0.0, err[2]], dtype=float)
            signed_radial = float(np.dot(horiz_err, radial_unit))
            tangential_vec = horiz_err - signed_radial * radial_unit
            tangential = float(np.linalg.norm(tangential_vec))
            signed_vertical = float(err[1])
            row = {
                "position_id": r["ID"],
                "config": cfg_map[label],
                "radial_mm": abs(signed_radial),
                "tangential_mm": tangential,
                "vertical_mm": abs(signed_vertical),
                "signed_radial": signed_radial,
                "signed_vertical": signed_vertical,
                "err_x_mm": float(err[0]),
                "err_z_mm": float(err[2]),
                "truth_x_mm": float(truth[0]),
                "truth_z_mm": float(truth[2]),
            }
            rows.append(row)
            vals.append(row)
        summary.append(
            {
                "config": cfg_map[label],
                "median_abs_radial": pct([v["radial_mm"] for v in vals], 50),
                "median_abs_tangential": pct([v["tangential_mm"] for v in vals], 50),
                "median_abs_vertical": pct([v["vertical_mm"] for v in vals], 50),
                "mean_signed_radial": float(np.nanmean([v["signed_radial"] for v in vals])),
                "mean_signed_vertical": float(np.nanmean([v["signed_vertical"] for v in vals])),
            }
        )
    write_csv(TABLES / "m1_error_direction.csv", rows)
    write_csv(TABLES / "m1_error_direction_summary.csv", summary)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(6, 6))
        colors = {"V4+C_V4+D_LOO": "tab:blue", "V5+C_V5+D_LOO": "tab:orange", "Vicon+C_cm+D_LOO": "tab:green"}
        for config, g in pd.DataFrame(rows).groupby("config"):
            ax.quiver(g["truth_x_mm"], g["truth_z_mm"], g["err_x_mm"], g["err_z_mm"], angles="xy", scale_units="xy", scale=1, color=colors.get(config, None), alpha=0.65, label=config)
        ax.scatter([centroid[0]], [centroid[2]], marker="x", color="black", label="anchor centroid")
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("z (mm)")
        ax.axis("equal")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(FIGURES / "m1_error_direction_polar.png", dpi=180)
        plt.close(fig)
    except Exception as exc:
        write_csv(TABLES / "m1_figure_skip.csv", [{"reason": repr(exc)}])
    v4 = next((r for r in summary if r["config"].startswith("V4")), {})
    v5 = next((r for r in summary if r["config"].startswith("V5")), {})
    key = f"V4 signed radial {v4.get('mean_signed_radial', float('nan')):.1f} mm vs V5 {v5.get('mean_signed_radial', float('nan')):.1f} mm"
    report_task("TASK_M1_ERROR_DIRECTION.md", "Task M1 - Error Direction Decomposition", summary, ["config", "median_abs_radial", "median_abs_tangential", "median_abs_vertical", "mean_signed_radial", "mean_signed_vertical"], key)
    return finish(ctx, "ok", key), summary


def task_m2(ctx_data: dict[str, Any], baselines: dict[str, pd.DataFrame]) -> tuple[TaskResult, list[dict[str, Any]]]:
    ctx = phase_context("M2")
    ext = ctx_data["ext"]
    inputs = ctx_data["inputs"]
    configs = ctx_data["configs"]
    ids = ctx_data["ids"]
    cache = TABLES / "m2_counterfactual_position_rows.csv"
    if cache.exists():
        cf = pd.read_csv(cache)
    else:
        jobs = [
            ext.make_static_job(job_id="m2_v5", config=configs["V5_CV5"], d_tag_mm=LOO_DTAG_MM, ids=ids, inputs=inputs, return_rows=True, meta={"case": "V5_CV5"}),
            ext.make_static_job(
                job_id="m2_vicon_v5delay",
                config=configs["Vicon_Ccm"],
                d_tag_mm=LOO_DTAG_MM,
                ids=ids,
                inputs=inputs,
                delays_override=configs["V5_CV5"].delays,
                return_rows=True,
                meta={"case": "Vicon_CV5"},
            ),
            ext.make_static_job(job_id="m2_vicon_cm", config=configs["Vicon_Ccm"], d_tag_mm=LOO_DTAG_MM, ids=ids, inputs=inputs, return_rows=True, meta={"case": "Vicon_Ccm"}),
        ]
        job_ctx = phase_context("M2_counterfactual_replay")
        res = run_static_jobs(jobs, job_ctx, "m2_counterfactuals", chunk_size=1)
        rows = []
        for r in res:
            for row in r["rows"]:
                rows.append({"case": r["meta"]["case"], **row})
        write_csv(cache, rows)
        cf = pd.DataFrame(rows)
    by_case = {case: g.set_index("ID") for case, g in cf.groupby("case")}
    sweep = pd.read_csv(TRANSFER_SWEEP)
    vc_sweep = sweep[(sweep["layout_source"] == "L_Vicon") & (sweep["correction_source"] == "C_Vicon_cm")]
    loo_row = pd.read_csv(TRANSFER_CELLS)
    loo = loo_row[(loo_row["layout_source"] == "L_Vicon") & (loo_row["correction_source"] == "C_Vicon_cm") & (loo_row["tag_delay_mode"] == "D_LOO_CV")]
    min_sweep = float(vc_sweep["median_3d_mm"].min()) if not vc_sweep.empty else float("nan")
    loo_med = float(loo.iloc[0]["median_3d_mm"]) if not loo.empty else float("nan")
    dtag_global = max(0.0, loo_med - min_sweep) if np.isfinite(loo_med) and np.isfinite(min_sweep) else float("nan")
    per_rows = []
    noise_vals = []
    for sid in ids:
        v5 = by_case["V5_CV5"].loc[sid]
        vc_v5d = by_case["Vicon_CV5"].loc[sid]
        vc_cm = by_case["Vicon_Ccm"].loc[sid]
        p_v5 = np.array([v5["solved_x_mm"], v5["solved_y_vertical_mm"], v5["solved_z_mm"]], dtype=float)
        p_vicon_v5d = np.array([vc_v5d["solved_x_mm"], vc_v5d["solved_y_vertical_mm"], vc_v5d["solved_z_mm"]], dtype=float)
        p_vicon_cm = np.array([vc_cm["solved_x_mm"], vc_cm["solved_y_vertical_mm"], vc_cm["solved_z_mm"]], dtype=float)
        truth = np.asarray(inputs["tag_truth_np"][sid], dtype=float)
        anchor_pos_err = float(np.linalg.norm(p_v5 - p_vicon_v5d))
        delay_err = float(np.linalg.norm(p_vicon_v5d - p_vicon_cm))
        coords = np.asarray(configs["V5_CV5"].coords, dtype=float)
        raw = ctx_data["raw_ranges"].get(sid, {})
        h_rows = []
        sig = []
        for aid in range(8):
            vals = np.asarray(raw.get(aid, []), dtype=float)
            if vals.size < 5:
                continue
            vec = truth - coords[aid]
            dist = float(np.linalg.norm(vec))
            if dist <= 1e-9:
                continue
            h_rows.append(vec / dist)
            # standard error of the median, Gaussian approximation
            sig.append(1.253 * float(np.nanstd(vals)) / math.sqrt(float(vals.size)))
        if len(h_rows) >= 4:
            H = np.vstack(h_rows)
            W = np.diag([1.0 / max(s * s, 1.0) for s in sig])
            try:
                cov = np.linalg.pinv(H.T @ W @ H)
                noise = float(math.sqrt(max(0.0, np.trace(cov))))
            except Exception:
                noise = float("nan")
        else:
            noise = float("nan")
        total = float(v5["err_3d_mm"])
        known = sum(v for v in [anchor_pos_err, delay_err, dtag_global, noise] if np.isfinite(v))
        nlos = max(0.0, total - known) if np.isfinite(total) else float("nan")
        interaction = total - known - nlos if np.isfinite(total) else float("nan")
        noise_vals.append(noise)
        per_rows.append(
            {
                "position_id": sid,
                "anchor_pos_err": anchor_pos_err,
                "delay_err": delay_err,
                "dtag_err": dtag_global,
                "noise_err": noise,
                "nlos_err": nlos,
                "interaction_err": interaction,
                "total_err": total,
                "gdop": float(pd.read_csv(FULL_V5_DOP).set_index("ID").loc[sid]["gdop"]),
            }
        )
    total_med = pct([r["total_err"] for r in per_rows], 50)
    budget = []
    for comp, col in [
        ("Anchor position error", "anchor_pos_err"),
        ("Delay model mismatch", "delay_err"),
        ("D_tag calibration residual", "dtag_err"),
        ("Range noise via GDOP", "noise_err"),
        ("NLOS systematic bias", "nlos_err"),
        ("Interaction / non-additivity", "interaction_err"),
        ("TOTAL", "total_err"),
    ]:
        med = pct([r[col] for r in per_rows], 50)
        budget.append({"component": comp, "median_contribution_mm": med, "fraction_of_total": med / total_med if total_med and np.isfinite(total_med) else float("nan")})
    write_csv(TABLES / "m2_error_budget.csv", budget)
    write_csv(TABLES / "m2_error_budget_per_position.csv", per_rows)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        b = [r for r in budget if r["component"] not in ("TOTAL",)]
        fig, ax = plt.subplots(figsize=(7.2, 3.8))
        ax.bar([r["component"] for r in b], [r["median_contribution_mm"] for r in b], color="tab:blue")
        ax.axhline(total_med, color="black", linestyle="--", label="V5 total median")
        ax.set_ylabel("median contribution (mm)")
        ax.tick_params(axis="x", rotation=35)
        ax.legend()
        fig.tight_layout()
        fig.savefig(FIGURES / "m2_error_budget_bar.png", dpi=180)
        plt.close(fig)
    except Exception as exc:
        write_csv(TABLES / "m2_figure_skip.csv", [{"reason": repr(exc)}])
    key = f"total {total_med:.1f} mm; anchor {budget[0]['median_contribution_mm']:.1f}, delay {budget[1]['median_contribution_mm']:.1f}, NLOS {budget[4]['median_contribution_mm']:.1f}"
    report_task("TASK_M2_ERROR_BUDGET.md", "Task M2 - Physical Error Budget", budget, ["component", "median_contribution_mm", "fraction_of_total"], key, extra="Components are counterfactual/proxy terms and are not strictly orthogonal.")
    return finish(ctx, "ok", key, "counterfactual/proxy non-orthogonal budget"), budget


def task_m3(ctx_data: dict[str, Any]) -> tuple[TaskResult, list[dict[str, Any]]]:
    ctx = phase_context("M3")
    truth = np.asarray(ctx_data["inputs"]["truth_coords"], dtype=float)
    configs = ctx_data["configs"]
    nlos = pd.read_csv(FULL_V5_STATIC_RHO).set_index("anchor_label")
    rows = []
    comp = []
    for label, coords in [("V5", configs["V5_CV5"].coords), ("V4", configs["V4_CV4"].coords)]:
        for aid, a in enumerate(ANCHORS):
            v = np.asarray(coords[aid], dtype=float) - truth[aid]
            mag = float(np.linalg.norm(v))
            az = math.degrees(math.atan2(v[2], v[0])) if mag > 0 else float("nan")
            el = math.degrees(math.atan2(v[1], math.hypot(v[0], v[2]))) if mag > 0 else float("nan")
            row = {
                "anchor_label": a,
                "layout": label,
                "layer": "lower" if aid in LOWER else "upper",
                "vx": float(v[0]),
                "vy": float(v[1]),
                "vz": float(v[2]),
                "magnitude_mm": mag,
                "direction_azimuth_deg": az,
                "direction_elevation_deg": el,
                "rho_rms_mm": float(nlos.loc[a]["rho_rms_mm"]) if a in nlos.index else float("nan"),
                "spike_rate_gt100": float(nlos.loc[a]["positive_spike_rate_gt100"]) if a in nlos.index else float("nan"),
            }
            rows.append(row)
    v5_rows = [r for r in rows if r["layout"] == "V5"]
    v4_rows = [r for r in rows if r["layout"] == "V4"]
    for aid, a in enumerate(ANCHORS):
        r5 = v5_rows[aid]
        r4 = v4_rows[aid]
        comp.append(
            {
                "anchor_label": a,
                "v5_magnitude": r5["magnitude_mm"],
                "v4_magnitude": r4["magnitude_mm"],
                "v5_direction": f"{r5['direction_azimuth_deg']:.1f}/{r5['direction_elevation_deg']:.1f}",
                "v4_direction": f"{r4['direction_azimuth_deg']:.1f}/{r4['direction_elevation_deg']:.1f}",
                "v5_minus_v4_magnitude": r5["magnitude_mm"] - r4["magnitude_mm"],
            }
        )
    # Direction concentration for V5 offsets.
    vecs = np.array([[r["vx"], r["vy"], r["vz"]] for r in v5_rows], dtype=float)
    unit = vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)
    mean_vec = unit.mean(axis=0)
    concentration = float(np.linalg.norm(mean_vec))
    for r in v5_rows:
        r["v5_direction_resultant"] = concentration
    write_csv(TABLES / "m3_phase_center_offset.csv", [r for r in rows if r["layout"] == "V5"])
    write_csv(TABLES / "m3_v4_vs_v5_offset.csv", comp)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(7, 5))
        ax = fig.add_subplot(111, projection="3d")
        ax.scatter(truth[:, 0], truth[:, 2], truth[:, 1], color="black", label="Vicon")
        for coords, color, lab in [(configs["V5_CV5"].coords, "tab:orange", "V5 offset"), (configs["V4_CV4"].coords, "tab:blue", "V4 offset")]:
            delta = coords - truth
            ax.quiver(truth[:, 0], truth[:, 2], truth[:, 1], delta[:, 0], delta[:, 2], delta[:, 1], color=color, length=1.0, normalize=False, label=lab)
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("z (mm)")
        ax.set_zlabel("y vertical (mm)")
        ax.legend()
        fig.tight_layout()
        fig.savefig(FIGURES / "m3_offset_vectors.png", dpi=180)
        plt.close(fig)
    except Exception as exc:
        write_csv(TABLES / "m3_figure_skip.csv", [{"reason": repr(exc)}])
    key = f"V5 offset mean {np.mean([r['magnitude_mm'] for r in v5_rows]):.1f} mm; direction resultant {concentration:.2f}"
    report_task("TASK_M3_PHASE_CENTER.md", "Task M3 - Vicon Phase-Center Offset", [r for r in rows if r["layout"] == "V5"], ["anchor_label", "layer", "magnitude_mm", "direction_azimuth_deg", "direction_elevation_deg", "rho_rms_mm"], key)
    return finish(ctx, "ok", key), rows


def corr_pair(x: list[float], y: list[float]) -> tuple[float, float]:
    xx = np.asarray(x, dtype=float)
    yy = np.asarray(y, dtype=float)
    mask = np.isfinite(xx) & np.isfinite(yy)
    if mask.sum() < 3:
        return float("nan"), float("nan")
    if stats is not None:
        res = stats.pearsonr(xx[mask], yy[mask])
        return float(res.statistic), float(res.pvalue)
    return float(np.corrcoef(xx[mask], yy[mask])[0, 1]), float("nan")


def task_m4(ctx_data: dict[str, Any]) -> tuple[TaskResult, list[dict[str, Any]]]:
    ctx = phase_context("M4")
    configs = ctx_data["configs"]
    v5_delays = configs["V5_CV5"].delays
    nlos = pd.read_csv(FULL_V5_STATIC_RHO).set_index("anchor_label")
    shap = pd.read_csv(GPU_SHAPLEY).set_index("anchor_label") if GPU_SHAPLEY.exists() else pd.DataFrame()
    rows = []
    for aid, a in enumerate(ANCHORS):
        e = float(v5_delays[aid]) - V5_COMMON_MODE_MM
        row = {
            "anchor_label": a,
            "anchor_id": aid,
            "e_i_mm": e,
            "spike_rate": float(nlos.loc[a]["positive_spike_rate_gt100"]) if a in nlos.index else float("nan"),
            "rms_rho": float(nlos.loc[a]["rho_rms_mm"]) if a in nlos.index else float("nan"),
            "median_abs_rho": abs(float(nlos.loc[a]["rho_median_mm"])) if a in nlos.index else float("nan"),
            "shapley": float(shap.loc[a]["shapley_3d"]) if not shap.empty and a in shap.index else float("nan"),
        }
        rows.append(row)
    for metric in ("spike_rate", "rms_rho", "median_abs_rho", "shapley"):
        r, p = corr_pair([x["e_i_mm"] for x in rows], [x[metric] for x in rows])
        for row in rows:
            row[f"corr_ei_{metric}"] = r
            row[f"corr_p_{metric}"] = p
    height_rows = []
    res_df = pd.DataFrame(ctx_data["residuals"]["V5_CV5"])
    for aid, g in res_df.groupby("anchor_id"):
        vals = {"LOW": float("nan"), "MID": float("nan"), "HIGH": float("nan")}
        for tier, gt in g.groupby("height_tier"):
            vals[str(tier)] = float(np.nanmean(gt["rho_mm"].to_numpy(dtype=float)))
        height_rows.append(
            {
                "anchor_label": ANCHORS[int(aid)],
                "e_i_mm": float(v5_delays[int(aid)]) - V5_COMMON_MODE_MM,
                "rho_low": vals.get("LOW", float("nan")),
                "rho_mid": vals.get("MID", float("nan")),
                "rho_high": vals.get("HIGH", float("nan")),
                "tier_range_mm": float(np.nanmax(list(vals.values())) - np.nanmin(list(vals.values()))),
            }
        )
    ext = ctx_data["ext"]
    inputs = ctx_data["inputs"]
    ids = ctx_data["ids"]
    cf_path = TABLES / "m4_counterfactual.csv"
    if cf_path.exists():
        cf_rows = pd.read_csv(cf_path).to_dict("records")
    else:
        c_delays = {aid: V5_COMMON_MODE_MM for aid in range(8)}
        job = ext.make_static_job(
            job_id="m4_v5_common_only",
            config=configs["V5_CV5"],
            d_tag_mm=LOO_DTAG_MM,
            ids=ids,
            inputs=inputs,
            delays_override=c_delays,
            return_rows=False,
            meta={"case": "all_ei_zero"},
        )
        job_ctx = phase_context("M4_counterfactual_common_only")
        result = run_static_jobs([job], job_ctx, "m4_counterfactual", chunk_size=1)[0]
        base = pd.read_csv(TRANSFER_CELLS)
        b = base[(base["layout_source"] == "L_V5") & (base["correction_source"] == "C_V5") & (base["tag_delay_mode"] == "D_LOO_CV")].iloc[0]
        cf_rows = [
            {"config": "V5_CV5_baseline", "median_3d": float(b["median_3d_mm"]), "rmse": float(b["rmse_3d_mm"]), "notes": "baseline e_i included"},
            {"config": "V5_CV5_all_ei_zero", "median_3d": result["summary"]["median_3d_mm"], "rmse": result["summary"]["rmse_3d_mm"], "notes": "all anchor delays forced to common-mode c"},
        ]
    write_csv(TABLES / "m4_ei_vs_nlos.csv", rows)
    write_csv(TABLES / "m4_ei_vs_height.csv", height_rows)
    write_csv(TABLES / "m4_counterfactual.csv", cf_rows)
    key = f"corr(e_i,rho_rms) {rows[0].get('corr_ei_rms_rho', float('nan')):.2f}; all-e_i-zero median {cf_rows[-1]['median_3d']:.1f} mm"
    report_task("TASK_M4_DELAY_NLOS_ABSORPTION.md", "Task M4 - V5 Delay Model NLOS Absorption", rows, ["anchor_label", "e_i_mm", "spike_rate", "rms_rho", "shapley", "corr_ei_rms_rho"], key)
    return finish(ctx, "ok", key), rows


def task_m5(ctx_data: dict[str, Any]) -> tuple[TaskResult, list[dict[str, Any]]]:
    ctx = phase_context("M5")
    cfg = ctx_data["configs"]["V5_CV5"]
    ids = ctx_data["ids"]
    truth_by_id = ctx_data["inputs"]["tag_truth_np"]
    rows = []
    for k in (4, 5, 6, 7, 8):
        subsets = list(combinations(range(8), k))
        # All subsets up to k=6, all 7-anchor jackknifes, and full 8.
        for idx, subset in enumerate(subsets):
            subset_ids = list(subset)
            dtag = calibrate_dtag_subset(ctx_data, cfg, ids, subset_ids)
            errs = []
            solved_n = 0
            for sid in ids:
                truth = np.asarray(truth_by_id[sid], dtype=float)
                x0 = truth.copy()
                p = solve_point_ls(ctx_data["medians"].get(sid, {}), cfg.coords, cfg.delays, dtag, subset_ids, x0)
                if p is None:
                    continue
                solved_n += 1
                errs.append(float(np.linalg.norm(p - truth)))
            ranges = k * (k - 1) // 2
            params = 4 * k - 6
            rows.append(
                {
                    "k": k,
                    "subset_id": idx,
                    "anchor_labels": "".join(ANCHORS[i] for i in subset_ids),
                    "ranges": ranges,
                    "params": params,
                    "redundancy": ranges - params,
                    "d_tag_mm": dtag,
                    "median_3d": pct(errs, 50),
                    "rmse": rmse(errs),
                    "n_solved": solved_n,
                    "method": "median_range_huber_ls",
                }
            )
    # Simulated ninth anchor using highest active-design candidate and synthetic unbiased range.
    sim_rows = []
    if GPU_CANDIDATES.exists():
        cand = pd.read_csv(GPU_CANDIDATES).sort_values("rank").iloc[0]
        a9 = np.array([cand["x"], cand["y"], cand["z"]], dtype=float)
        coords9 = np.vstack([cfg.coords, a9])
        delays9 = {**cfg.delays, 8: float(np.nanmedian(list(cfg.delays.values())))}
        anchor_ids9 = list(range(9))
        dtag9 = LOO_DTAG_MM
        errs8 = []
        errs9 = []
        for sid in ids:
            truth = np.asarray(truth_by_id[sid], dtype=float)
            m8 = dict(ctx_data["medians"].get(sid, {}))
            p8 = solve_point_ls(m8, cfg.coords, cfg.delays, LOO_DTAG_MM, list(range(8)), truth)
            if p8 is not None:
                errs8.append(float(np.linalg.norm(p8 - truth)))
            m9 = dict(m8)
            m9[8] = float(np.linalg.norm(truth - a9) + delays9[8] + dtag9)
            p9 = solve_point_ls(m9, coords9, delays9, dtag9, anchor_ids9, truth)
            if p9 is not None:
                errs9.append(float(np.linalg.norm(p9 - truth)))
        sim_rows = [
            {"config": "V5_8anchor_median_ls", "n_anchors": 8, "median_3d": pct(errs8, 50), "improvement_mm": 0.0, "notes": "median-range Huber LS reference"},
            {"config": "V5_plus_active_design_anchor", "n_anchors": 9, "median_3d": pct(errs9, 50), "improvement_mm": pct(errs8, 50) - pct(errs9, 50), "notes": f"synthetic unbiased anchor at rank-1 candidate ({a9[0]:.1f},{a9[1]:.1f},{a9[2]:.1f})"},
        ]
    else:
        sim_rows = [{"config": "skipped", "n_anchors": 9, "median_3d": float("nan"), "improvement_mm": float("nan"), "notes": f"missing {GPU_CANDIDATES}"}]
    ident = []
    df = pd.DataFrame(rows)
    for k, g in df.groupby("k"):
        ident.append(
            {
                "k": int(k),
                "ranges": int(k * (k - 1) // 2),
                "params": int(4 * k - 6),
                "redundancy": int(k * (k - 1) // 2 - (4 * k - 6)),
                "mean_median_3d": float(np.nanmean(g["median_3d"].to_numpy(dtype=float))),
                "best_median_3d": float(np.nanmin(g["median_3d"].to_numpy(dtype=float))),
            }
        )
    ident.append({"k": 9, "ranges": 36, "params": 30, "redundancy": 6, "mean_median_3d": sim_rows[-1]["median_3d"], "best_median_3d": sim_rows[-1]["median_3d"]})
    write_csv(TABLES / "m5_anchor_count_accuracy.csv", rows)
    write_csv(TABLES / "m5_ninth_anchor_simulation.csv", sim_rows)
    write_csv(TABLES / "m5_identifiability_table.csv", ident)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(6.5, 3.8))
        ax.errorbar([r["k"] for r in ident if r["k"] <= 8], [r["mean_median_3d"] for r in ident if r["k"] <= 8], marker="o")
        ax.scatter([9], [ident[-1]["mean_median_3d"]], color="tab:orange", label="synthetic 9th")
        ax.set_xlabel("anchor count")
        ax.set_ylabel("median 3D (mm)")
        ax.legend()
        fig.tight_layout()
        fig.savefig(FIGURES / "m5_accuracy_vs_anchors.png", dpi=180)
        plt.close(fig)
    except Exception as exc:
        write_csv(TABLES / "m5_figure_skip.csv", [{"reason": repr(exc)}])
    key = f"8-anchor redundancy +2; 9-anchor redundancy +6; simulated 9-anchor median {sim_rows[-1]['median_3d']:.1f} mm"
    report_task("TASK_M5_ANCHOR_COUNT.md", "Task M5 - Anchor Count vs Identifiability", ident, ["k", "ranges", "params", "redundancy", "mean_median_3d", "best_median_3d"], key, extra="Subset accuracy uses a median-range Huber LS replay, not the per-frame C trajectory solver.")
    return finish(ctx, "ok", key, "subset replay uses median-range LS"), rows


def task_m6() -> tuple[TaskResult, list[dict[str, Any]]]:
    ctx = phase_context("M6")
    require_path(ROTO_R6, "ROTO deep-dive R6 aggregate")
    agg = pd.read_csv(ROTO_R6)
    err_path = ROTO_DEEP / "tables/r6_phase_error.csv"
    if err_path.exists():
        shutil.copy2(err_path, TABLES / "m6_roto_phase_error.csv")
    rows = agg.to_dict("records")
    # Add std by sector from detailed table when available.
    if err_path.exists():
        detailed = pd.read_csv(err_path)
        std_map = detailed.groupby("sector_deg")["median_3d"].std().to_dict()
        for r in rows:
            r["std_median_3d"] = float(std_map.get(r["sector_deg"], float("nan")))
    write_csv(TABLES / "m6_roto_phase_aggregate.csv", rows)
    if (ROTO_DEEP / "figures/r6_polar_error.png").exists():
        shutil.copy2(ROTO_DEEP / "figures/r6_polar_error.png", FIGURES / "m6_roto_phase_polar.png")
    worst = max(rows, key=lambda r: float(r["mean_median_3d"]))
    key = f"worst sector {worst['sector_deg']} deg, {worst['mean_median_3d']:.1f} mm, anchor {worst['worst_anchor']}"
    report_task("TASK_M6_ROTO_PHASE.md", "Task M6 - ROTO Per-Rotation-Phase Error", rows, ["sector_deg", "mean_median_3d", "std_median_3d", "worst_anchor"], key)
    return finish(ctx, "ok", key), rows


def task_m7() -> tuple[TaskResult, list[dict[str, Any]]]:
    ctx = phase_context("M7")
    rows = []
    summary_rows = []
    if ROTO_R3_SUMMARY.exists():
        r3 = pd.read_csv(ROTO_R3_SUMMARY)
        for _, r in r3.iterrows():
            summary_rows.append(
                {
                    "method": str(r["method"]),
                    "overall_median_3d": float(r["overall_median"]),
                    "overall_rmse": float(r["overall_rmse"]),
                    "inter_tag_distance_error_mm": float(r["baseline_error_mm"]),
                    "notes": "carried over from ROTO deep-dive R3",
                }
            )
        if ROTO_R3_DTAG.exists():
            dtag = pd.read_csv(ROTO_R3_DTAG).to_dict("records")
            write_csv(TABLES / "m7_estimated_dtag.csv", dtag)
    else:
        summary_rows.append({"method": "skipped", "overall_median_3d": float("nan"), "overall_rmse": float("nan"), "inter_tag_distance_error_mm": float("nan"), "notes": f"missing {ROTO_R3_SUMMARY}"})
    if ROTO_SAMPLES.exists():
        samples = pd.read_csv(ROTO_SAMPLES)
        for (cid, tag), g in samples.groupby(["capture_id", "tag"]):
            rows.append({"capture_id": cid, "method": "unconstrained", "tag": tag, "median_3d": pct(g["err3d_mm"], 50), "rmse": rmse(g["err3d_mm"])})
    write_csv(TABLES / "m7_rigid_constraint.csv", rows)
    write_csv(TABLES / "m7_rigid_summary.csv", summary_rows)
    key = f"constraint diagnostic best median {min([r['overall_median_3d'] for r in summary_rows if np.isfinite(r['overall_median_3d'])] or [float('nan')]):.1f} mm"
    report_task("TASK_M7_RIGID_CONSTRAINT.md", "Task M7 - ROTO Inter-Tag Rigid Constraint", summary_rows, ["method", "overall_median_3d", "overall_rmse", "inter_tag_distance_error_mm", "notes"], key, extra="This reuses the ROTO deep-dive diagnostic projection; a true range-level constrained optimizer remains future work.")
    return finish(ctx, "ok", key, "diagnostic projection reused"), summary_rows


def task_m8(ctx_data: dict[str, Any], baselines: dict[str, pd.DataFrame]) -> tuple[TaskResult, list[dict[str, Any]]]:
    ctx = phase_context("M8")
    v5 = baselines["V5_CV5"].set_index("ID")
    dop = pd.read_csv(FULL_V5_DOP).set_index("ID")
    res = pd.DataFrame(ctx_data["residuals"]["V5_CV5"])
    rows = []
    for sid, r in v5.iterrows():
        rr = res[res["position_id"] == sid]
        theta = rr["theta_deg"].to_numpy(dtype=float)
        rows.append(
            {
                "position_id": sid,
                "error_3d": float(r["err_3d_mm"]),
                "gdop": float(dop.loc[sid]["gdop"]) if sid in dop.index else float("nan"),
                "n_good_anchors": int(np.sum(np.abs(rr["rho_mm"].to_numpy(dtype=float)) < 50.0)),
                "n_bad_anchors": int(np.sum(rr["rho_mm"].to_numpy(dtype=float) > 100.0)),
                "height": float(r["truth_y_vertical_mm"]),
                "height_tier": str(ctx_data["maps"]["height"].get(sid, "")),
                "dist_centroid": float(r["distance_to_array_centroid_mm"]),
                "elev_diversity": float(np.nanstd(theta)) if theta.size else float("nan"),
                "dtag_loo_fold_residual": float(np.nanmedian(rr["effective_dtag_mm"].to_numpy(dtype=float)) - LOO_DTAG_MM) if len(rr) else float("nan"),
                "truth_x_mm": float(r["truth_x_mm"]),
                "truth_z_mm": float(r["truth_z_mm"]),
            }
        )
    df = pd.DataFrame(rows)
    predictors = ["gdop", "n_bad_anchors", "height", "dist_centroid", "elev_diversity", "dtag_loo_fold_residual"]
    reg_rows = []
    y = df["error_3d"].to_numpy(dtype=float)
    for pred in predictors:
        fit = linreg(df[pred], y)
        # partial R2 as drop in simple residual variance relative to intercept model.
        reg_rows.append({"predictor": pred, "beta": fit["beta"], "p_value": fit["p_value"], "partial_r2": fit["r2"], "n": fit["n"]})
    write_csv(TABLES / "m8_position_anatomy.csv", rows)
    write_csv(TABLES / "m8_regression.csv", reg_rows)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(5.2, 4.6))
        sc = ax.scatter(df["truth_x_mm"], df["truth_z_mm"], c=df["error_3d"], cmap="viridis", s=80)
        for _, r in df.iterrows():
            ax.text(r["truth_x_mm"], r["truth_z_mm"], r["position_id"], fontsize=7)
        fig.colorbar(sc, ax=ax, label="3D error (mm)")
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("z (mm)")
        ax.axis("equal")
        fig.tight_layout()
        fig.savefig(FIGURES / "m8_position_error_map.png", dpi=180)
        plt.close(fig)
    except Exception as exc:
        write_csv(TABLES / "m8_figure_skip.csv", [{"reason": repr(exc)}])
    best_pred = max(reg_rows, key=lambda r: -1 if not np.isfinite(r["partial_r2"]) else r["partial_r2"])
    key = f"strongest simple predictor {best_pred['predictor']} R2={best_pred['partial_r2']:.2f}"
    extra = f"Easy positions (<50 mm): {int((df['error_3d'] < 50).sum())}; hard positions (>100 mm): {int((df['error_3d'] > 100).sum())}."
    report_task("TASK_M8_POSITION_ANATOMY.md", "Task M8 - Per-Position Error Anatomy", reg_rows, ["predictor", "beta", "p_value", "partial_r2", "n"], key, extra=extra)
    return finish(ctx, "ok", key), rows


def gauge_basis(params: list[str], anchors: np.ndarray, tag: np.ndarray) -> np.ndarray:
    n = len(params)
    vecs = []
    # translations x/y/z applied to anchors and tag.
    for dim in range(3):
        v = np.zeros(n)
        for i in range(8):
            v[3 * i + dim] = 1.0
        tag_start = 24 + 8 + 1
        v[tag_start + dim] = 1.0
        vecs.append(v)
    center = anchors.mean(axis=0)
    axes = np.eye(3)
    for axis in axes:
        v = np.zeros(n)
        for i in range(8):
            rel = anchors[i] - center
            delta = np.cross(axis, rel)
            v[3 * i : 3 * i + 3] = delta
        delta_tag = np.cross(axis, tag - center)
        tag_start = 24 + 8 + 1
        v[tag_start : tag_start + 3] = delta_tag
        vecs.append(v)
    B = np.vstack(vecs).T
    norms = np.linalg.norm(B, axis=0)
    return B[:, norms > 1e-12]


def task_m9(ctx_data: dict[str, Any]) -> tuple[TaskResult, list[dict[str, Any]]]:
    ctx = phase_context("M9")
    cfg = ctx_data["configs"]["V5_CV5"]
    sid = "ID12" if "ID12" in ctx_data["ids"] else ctx_data["ids"][len(ctx_data["ids"]) // 2]
    anchors = np.asarray(cfg.coords, dtype=float)
    tag = np.asarray(ctx_data["inputs"]["tag_truth_np"][sid], dtype=float)
    params = []
    for i in range(8):
        for d in "xyz":
            params.append(f"A{ANCHORS[i]}_{d}")
    for i in range(8):
        params.append(f"d_{ANCHORS[i]}")
    params.append("D_tag")
    for d in "xyz":
        params.append(f"tag_{d}")
    J_rows = []
    # inter-anchor ranges
    for i, j in combinations(range(8), 2):
        row = np.zeros(len(params))
        diff = anchors[i] - anchors[j]
        dist = max(float(np.linalg.norm(diff)), 1e-9)
        u = diff / dist
        row[3 * i : 3 * i + 3] = u
        row[3 * j : 3 * j + 3] = -u
        row[24 + i] = 1.0
        row[24 + j] = 1.0
        J_rows.append(row)
    # tag-anchor ranges
    tag_start = 24 + 8 + 1
    for i in range(8):
        row = np.zeros(len(params))
        diff = tag - anchors[i]
        dist = max(float(np.linalg.norm(diff)), 1e-9)
        u = diff / dist
        row[3 * i : 3 * i + 3] = -u
        row[24 + i] = 1.0
        row[24 + 8] = 1.0
        row[tag_start : tag_start + 3] = u
        J_rows.append(row)
    J = np.vstack(J_rows)
    B = gauge_basis(params, anchors, tag)
    if linalg is not None:
        Z = linalg.null_space(B.T)
    else:
        _, _, vt = np.linalg.svd(B.T)
        Z = vt[B.shape[1] :].T
    Jr = J @ Z
    F = Jr.T @ Jr + 1e-12 * np.eye(Jr.shape[1])
    eigvals, eigvecs_r = np.linalg.eigh(F)
    order = np.argsort(eigvals)
    rows = []
    full_rows = []
    scale_axis = np.zeros(len(params))
    center = anchors.mean(axis=0)
    for i in range(8):
        scale_axis[3 * i : 3 * i + 3] = anchors[i] - center
    c_axis = np.zeros(len(params))
    c_axis[24 : 24 + 8] = 1.0
    d_axis = np.zeros(len(params))
    d_axis[24 + 8] = 1.0
    z_axis = np.zeros(len(params))
    xy_axis = np.zeros(len(params))
    for i in range(8):
        z_axis[3 * i + 1] = 1.0
        xy_axis[3 * i] = 1.0
        xy_axis[3 * i + 2] = 1.0

    def proj(v: np.ndarray, axis: np.ndarray) -> float:
        den = float(np.linalg.norm(axis) * np.linalg.norm(v))
        return float(np.dot(v, axis) / den) if den > 1e-12 else float("nan")

    for rank, idx in enumerate(order[:3], start=1):
        v = Z @ eigvecs_r[:, idx]
        rows.append(
            {
                "rank": rank,
                "eigenvalue": float(eigvals[idx]),
                "proj_scale": proj(v, scale_axis),
                "proj_c": proj(v, c_axis),
                "proj_dtag": proj(v, d_axis),
                "proj_z_mean": proj(v, z_axis),
                "proj_xy_mean": proj(v, xy_axis),
                "representative_position": sid,
            }
        )
        for name, comp in zip(params, v):
            full_rows.append({"rank": rank, "param_name": name, "component_value": float(comp)})
    write_csv(TABLES / "m9_fisher_eigenvectors.csv", rows)
    write_csv(TABLES / "m9_fisher_full_eigenvector.csv", full_rows)
    key = f"weakest eig {rows[0]['eigenvalue']:.2e}; proj scale {rows[0]['proj_scale']:.2f}, c {rows[0]['proj_c']:.2f}, Dtag {rows[0]['proj_dtag']:.2f}"
    report_task("TASK_M9_FISHER_EIGENVECTOR.md", "Task M9 - Cancellation Valley Direction From Fisher Eigenvector", rows, ["rank", "eigenvalue", "proj_scale", "proj_c", "proj_dtag", "proj_z_mean", "proj_xy_mean"], key)
    return finish(ctx, "ok", key), rows


def read_value(path: Path, filters: dict[str, Any], column: str) -> float:
    if not path.exists():
        return float("nan")
    df = pd.read_csv(path)
    for col, val in filters.items():
        if col not in df.columns:
            return float("nan")
        df = df[df[col].astype(str) == str(val)]
    if df.empty or column not in df.columns:
        return float("nan")
    return float(df.iloc[0][column])


def task_m10() -> tuple[TaskResult, list[dict[str, Any]]]:
    ctx = phase_context("M10")
    dtag_rows = []
    sources = [
        ("FULL_V5_static_DLOO", FULL_V5_STATIC, {}, "tag_delay_value_mm"),
        ("Transfer_LV5_CV5_DLOO", TRANSFER_CELLS, {"layout_source": "L_V5", "correction_source": "C_V5", "tag_delay_mode": "D_LOO_CV"}, "tag_delay_value_mm"),
        ("Transfer_LV4_CV4_DLOO", TRANSFER_CELLS, {"layout_source": "L_V4", "correction_source": "C_V4", "tag_delay_mode": "D_LOO_CV"}, "tag_delay_value_mm"),
    ]
    for name, path, filt, col in sources:
        if path == FULL_V5_STATIC and path.exists():
            df = pd.read_csv(path)
            val = float(df[df["tag_delay_mode"] == "D_LOO_CV"]["tag_delay_value_mm"].iloc[0])
        else:
            val = read_value(path, filt, col)
        dtag_rows.append({"source": name, "config": str(filt), "d_tag_mm": val, "delta_vs_49_621_mm": val - LOO_DTAG_MM if np.isfinite(val) else float("nan")})
    # Add extended and follow-up D_tag sources when present.
    for path, label, col in [
        (ANALYSIS / "FULL_V5_extended_mechanism_ablations/tables/item01_per_tier_range_residual_dtag.csv", "extended_item01", "d_tag_range_residual_mm"),
        (ANALYSIS / "FULL_V5_extended_mechanism_ablations/tables/item04_nlos_excluded_dtag.csv", "extended_item04", "d_tag_mm"),
        (OVERNIGHT_N4, "overnight_N4_p30", "d_tag_mm"),
    ]:
        if path.exists():
            df = pd.read_csv(path)
            for _, r in df.head(20).iterrows():
                if col in r:
                    dtag_rows.append({"source": label, "config": str(r.to_dict())[:180], "d_tag_mm": float(r[col]) if pd.notna(r[col]) else float("nan"), "delta_vs_49_621_mm": float(r[col]) - LOO_DTAG_MM if pd.notna(r[col]) else float("nan")})
    med_rows = []
    med_sources = [
        ("FULL_V5_static_per_position", FULL_V5_STATIC, "row_median", 0.0),
        ("Transfer_LV5_CV5_DLOO", TRANSFER_CELLS, "median_3d_mm", 0.0),
        ("Final_static_comparison", ANALYSIS / "FULL_V4_vs_V5_final/tables/final_v4_vs_v5_static_comparison.csv", "median_3d_mm", 0.0),
    ]
    baseline = float("nan")
    if TRANSFER_CELLS.exists():
        t = pd.read_csv(TRANSFER_CELLS)
        m = t[(t["layout_source"] == "L_V5") & (t["correction_source"] == "C_V5") & (t["tag_delay_mode"] == "D_LOO_CV")]
        if not m.empty:
            baseline = float(m.iloc[0]["median_3d_mm"])
    for name, path, col, _ in med_sources:
        val = float("nan")
        if path.exists():
            df = pd.read_csv(path)
            if name == "FULL_V5_static_per_position":
                sub = df[df["tag_delay_mode"] == "D_LOO_CV"]
                val = float(np.nanmedian(sub["err_3d_mm"].to_numpy(dtype=float)))
            elif name == "Transfer_LV5_CV5_DLOO":
                sub = df[(df["layout_source"] == "L_V5") & (df["correction_source"] == "C_V5") & (df["tag_delay_mode"] == "D_LOO_CV")]
                val = float(sub.iloc[0][col]) if not sub.empty else float("nan")
            elif col in df.columns:
                # Best-effort final-table row for the V5 LOO baseline, not V5 D0.
                if "case" in df.columns:
                    sub = df[df["case"].astype(str).str.contains("V5 D_LOO_CV", case=False, regex=False)]
                else:
                    sub = df[df.astype(str).apply(lambda s: s.str.contains("V5", case=False, regex=False)).any(axis=1)]
                val = float(sub.iloc[0][col]) if not sub.empty else float("nan")
        med_rows.append({"source": name, "median_3d_mm": val, "delta_vs_transfer_baseline_mm": val - baseline if np.isfinite(val) and np.isfinite(baseline) else float("nan")})
    evidence = []
    def add_claim(cid: int, text: str, support: str, contra: str, conf: str) -> None:
        evidence.append({"claim_id": cid, "claim_text": text, "supporting_tasks": support, "supporting_numbers": support, "contradicting_evidence": contra, "confidence": conf})

    scale = pd.read_csv(V5_SCALE_COMP) if V5_SCALE_COMP.exists() else pd.DataFrame()
    scale_txt = ""
    if not scale.empty and {"layout", "sim3_scale"}.issubset(scale.columns):
        scale_txt = "; ".join(f"{r['layout']} scale={float(r['sim3_scale']):.3f}" for _, r in scale.iterrows())
    add_claim(1, "V5 fixes scale", scale_txt or "missing scale table", "", "strong" if scale_txt else "weak")
    add_claim(2, "V4 wins on this dataset", "V4+C_V4 LOO 57.9 mm vs V5+C_V5 LOO 67.8 mm", "p30 fixed-delay can be lower but is not same deployable calibration", "strong")
    add_claim(3, "Cancellation valley exists", "mechanism C, extended item06/item07, M9 Fisher projections", "", "strong")
    add_claim(4, "D/F are NLOS-heavy but geometrically essential", "FULL_V5 rho table + GPU Shapley table", "M4 tests whether e_i absorbs NLOS", "moderate")
    add_claim(5, "p30 improvement is cancellation-sensitive", "follow-up/overnight N4 and item21; fixed p30 vs recalibrated p30 differ", "", "moderate")
    auc = read_value(GPU_TASK6, {}, "pr_auc") if GPU_TASK6.exists() else float("nan")
    add_claim(6, "NLOS detectable from range statistics", f"GPU task6 PR-AUC={auc:.3f}" if np.isfinite(auc) else "GPU task6 table present", "feature leakage checks required", "moderate")
    add_claim(7, "Student-t is correct noise model", "GPU task11 BIC winner M2_student_t", "", "moderate")
    add_claim(8, "AA-AT asymmetry is small", "GPU task4 mean asymmetry about -4.7 mm", "", "moderate")
    add_claim(9, "Per-tag D_tag varies materially", "ROTO deep-dive Dtag estimates differ by about 45.7 mm", "static mechanism previously estimated smaller delta", "moderate")
    add_claim(10, "V5 transferability supported by MC", "Batch2 N1 corrected/adversarial simulation; use report for P(V5<V4)", "original P=1.00 was questionable", "moderate")
    write_csv(TABLES / "m10_dtag_consistency.csv", dtag_rows)
    write_csv(TABLES / "m10_median3d_consistency.csv", med_rows)
    write_csv(TABLES / "m10_evidence_matrix.csv", evidence)
    max_delta = max([abs(r["delta_vs_transfer_baseline_mm"]) for r in med_rows if np.isfinite(r["delta_vs_transfer_baseline_mm"])] or [float("nan")])
    key = f"V5 baseline consistency max delta {max_delta:.2f} mm"
    report_task("TASK_M10_CONSISTENCY_AUDIT.md", "Task M10 - Comprehensive Consistency Audit", evidence, ["claim_id", "claim_text", "confidence", "contradicting_evidence"], key)
    return finish(ctx, "ok", key), evidence


def report_task(filename: str, title: str, rows: list[dict[str, Any]], cols: list[str], key: str, extra: str = "") -> None:
    lines = [f"# {title}\n\n", f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n", f"Key finding: {key}\n\n"]
    if extra:
        lines.append(extra + "\n\n")
    if rows:
        append_md_table(lines, rows, cols, max_rows=40)
    (REPORTS / filename).write_text("".join(lines), encoding="utf-8")


def task_status_row(res: TaskResult) -> dict[str, Any]:
    return {
        "task": res.task,
        "status": res.status,
        "elapsed_s": res.elapsed_s,
        "mean_cpu_percent": res.mean_cpu_percent,
        "max_cpu_percent": res.max_cpu_percent,
        "workers": WORKERS,
        "key_finding": res.key_finding,
        "notes": res.notes,
    }


def run_task(name: str, fn, *args) -> tuple[TaskResult, Any]:
    checkpoint = TABLES / f"checkpoint_{name.lower()}_done.txt"
    try:
        result, payload = fn(*args)
        checkpoint.write_text(datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8")
        print(json.dumps(task_status_row(result), sort_keys=True), flush=True)
        return result, payload
    except Exception as exc:
        ctx = phase_context(name)
        result = finish(ctx, "failed", f"failed: {exc!r}", repr(exc))
        write_json(REPORTS / f"{name.lower()}_failure.json", {"error": repr(exc)})
        print(json.dumps(task_status_row(result), sort_keys=True), flush=True)
        return result, None


def final_report(results: list[TaskResult]) -> None:
    rows = [task_status_row(r) for r in results]
    write_csv(TABLES / "mechanistic_deepdive_task_status_summary.csv", rows)
    lines = ["# Mechanistic Deep-Dive Completion\n\n", f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n"]
    append_md_table(lines, rows, ["task", "status", "elapsed_s", "key_finding", "notes"])
    lines.append("## Paper Cross-References\n\n")
    refs = [
        {"paper_section": "Scale-delay mechanism", "tasks": "M1, M3, M9", "tables": "m1_error_direction_summary.csv; m3_phase_center_offset.csv; m9_fisher_eigenvectors.csv"},
        {"paper_section": "Error budget", "tasks": "M2, M8", "tables": "m2_error_budget.csv; m8_position_anatomy.csv"},
        {"paper_section": "NLOS and delay absorption", "tasks": "M4, M6", "tables": "m4_ei_vs_nlos.csv; m6_roto_phase_aggregate.csv"},
        {"paper_section": "Anchor-count identifiability", "tasks": "M5", "tables": "m5_identifiability_table.csv"},
        {"paper_section": "Dynamic tracking limitations", "tasks": "M6, M7", "tables": "m6_roto_phase_aggregate.csv; m7_rigid_summary.csv"},
        {"paper_section": "Consistency audit", "tasks": "M10", "tables": "m10_evidence_matrix.csv"},
    ]
    append_md_table(lines, refs, ["paper_section", "tasks", "tables"])
    total = sum(r.elapsed_s for r in results)
    lines.append("## Runtime Self-Report\n\n")
    lines.append(f"Workers: {WORKERS}, CPU-only, total task wall sum: {total:.1f} s\n\n")
    append_md_table(lines, rows, ["task", "elapsed_s", "mean_cpu_percent", "max_cpu_percent", "workers"])
    (REPORTS / "MECHANISTIC_DEEPDIVE_COMPLETION.md").write_text("".join(lines), encoding="utf-8")


def verify_script() -> None:
    py_compile.compile(str(THIS), doraise=True)
    tree = ast.parse(THIS.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    forbidden = [name for name in imports if name.split(".")[0] in {"torch", "cupy"}]
    write_json(REPORTS / "SCRIPT_VERIFICATION.json", {"compiles": True, "forbidden_imports": forbidden})


def main() -> None:
    make_dirs()
    ctx_data = load_context()
    baselines = run_static_baselines(ctx_data)
    results: list[TaskResult] = []
    m1, _ = run_task("M1", task_m1, ctx_data, baselines); results.append(m1)
    m2, _ = run_task("M2", task_m2, ctx_data, baselines); results.append(m2)
    m3, _ = run_task("M3", task_m3, ctx_data); results.append(m3)
    m4, _ = run_task("M4", task_m4, ctx_data); results.append(m4)
    m5, _ = run_task("M5", task_m5, ctx_data); results.append(m5)
    m6, _ = run_task("M6", task_m6); results.append(m6)
    m7, _ = run_task("M7", task_m7); results.append(m7)
    m8, _ = run_task("M8", task_m8, ctx_data, baselines); results.append(m8)
    m9, _ = run_task("M9", task_m9, ctx_data); results.append(m9)
    m10, _ = run_task("M10", task_m10); results.append(m10)
    verify_script()
    final_report(results)
    print(f"Completion report: {REPORTS / 'MECHANISTIC_DEEPDIVE_COMPLETION.md'}", flush=True)


if __name__ == "__main__":
    main()
