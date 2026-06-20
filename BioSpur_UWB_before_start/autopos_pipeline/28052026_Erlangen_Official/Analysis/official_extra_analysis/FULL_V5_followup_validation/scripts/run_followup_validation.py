#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import ast
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from multiprocessing import get_context
from pathlib import Path
from typing import Any

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

import numpy as np
import pandas as pd
import psutil

THIS = Path(__file__).resolve()
ANALYSIS = THIS.parents[2]
BASE = THIS.parents[4]
OUT_ROOT = ANALYSIS / "FULL_V5_followup_validation"
TABLES = OUT_ROOT / "tables"
REPORTS = OUT_ROOT / "reports"
SCRIPTS = OUT_ROOT / "scripts"
EXT_SCRIPT = ANALYSIS / "FULL_V5_extended_mechanism_ablations/scripts/run_extended_mechanism_ablations.py"
FULL_V5_SCRIPT = ANALYSIS / "FULL_V5/scripts/run_full_v5_ablation_pipeline.py"
FULL_V5_ROTO_TRACK = ANALYSIS / "FULL_V5/tables/roto_track_summary.csv"

WORKERS = 6
LOO_DTAG_MM = 49.621
STATIC_TAG = "BSF66F"
ROTO_TAGS = ("BS2DCE", "BSDC91")
ANCHORS = tuple("ABCDEFGH")
PERCENTILES = (10, 20, 25, 30, 40, 50, 60, 75, 90)

_EXT = None
_FULL = None
_AB = None


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def require_path(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"missing required input for {label}: {path}")
    return path


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
        for row in rows:
            writer.writerow(row)


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


def linreg_slope_r2(x: Any, y: Any, slope_scale: float = 1.0) -> tuple[float, float]:
    xx = np.asarray(x, dtype=float)
    yy = np.asarray(y, dtype=float)
    mask = np.isfinite(xx) & np.isfinite(yy)
    xx = xx[mask]
    yy = yy[mask]
    if xx.size < 3 or float(np.nanstd(xx)) <= 1e-12:
        return float("nan"), float("nan")
    slope, intercept = np.polyfit(xx, yy, 1)
    pred = slope * xx + intercept
    ss_res = float(np.sum((yy - pred) ** 2))
    ss_tot = float(np.sum((yy - np.mean(yy)) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    return float(slope * slope_scale), r2


def phase_context(name: str) -> dict[str, Any]:
    psutil.cpu_percent(interval=None)
    return {
        "task": name,
        "start": time.perf_counter(),
        "cpu": [],
        "workers": WORKERS,
        "physical_cores": psutil.cpu_count(logical=False) or 0,
        "logical_cores": psutil.cpu_count(logical=True) or os.cpu_count() or 0,
    }


def sample_cpu(ctx: dict[str, Any]) -> None:
    ctx["cpu"].append(float(psutil.cpu_percent(interval=0.0)))


def finish_phase(ctx: dict[str, Any]) -> dict[str, Any]:
    sample_cpu(ctx)
    samples = ctx["cpu"] or [0.0]
    return {
        "task": ctx["task"],
        "elapsed_s": time.perf_counter() - ctx["start"],
        "mean_cpu_percent": float(np.nanmean(samples)),
        "max_cpu_percent": float(np.nanmax(samples)),
        "workers": ctx["workers"],
        "physical_cores": ctx["physical_cores"],
        "logical_cores": ctx["logical_cores"],
    }


def get_existing_modules():
    global _EXT, _FULL, _AB
    if _EXT is None:
        _EXT = load_module(require_path(EXT_SCRIPT, "extended mechanism script"), f"followup_ext_{os.getpid()}")
    if _FULL is None or _AB is None:
        _FULL, _AB = _EXT.worker_context()[1:]
    return _EXT, _FULL, _AB


def build_context() -> dict[str, Any]:
    ext = load_module(require_path(EXT_SCRIPT, "extended mechanism script"), "followup_ext_main")
    mech, full = ext.load_previous_modules()
    inputs, configs, assignments, maps = ext.build_inputs_and_configs(mech, full)
    static_files = [Path(p) for p in inputs["static_files"]]
    raw_ranges, raw_info = ext.load_raw_ranges(static_files)
    if not raw_ranges:
        raise RuntimeError("raw static ranges not found; expected tr_all.csv under static_ID01-ID24 captures")
    ids = sorted(inputs["tag_truth_np"])
    return {
        "ext": ext,
        "mech": mech,
        "full": full,
        "inputs": inputs,
        "configs": configs,
        "assignments": assignments,
        "maps": maps,
        "ids": ids,
        "raw_ranges": raw_ranges,
        "raw_info": raw_info,
    }


def percentile_ranges(raw_ranges: dict[str, dict[int, np.ndarray]], percentile_value: float) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    for sid, by_anchor in raw_ranges.items():
        out[sid] = {}
        for aid, vals in by_anchor.items():
            arr = finite(vals)
            if arr.size:
                out[sid][int(aid)] = float(np.nanpercentile(arr, percentile_value))
    return out


def hybrid_ranges(
    p50_ranges: dict[str, dict[int, float]],
    p30_ranges: dict[str, dict[int, float]],
    p30_anchor_ids: set[int],
) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    for sid in sorted(p50_ranges):
        out[sid] = {}
        for aid in range(8):
            src = p30_ranges if aid in p30_anchor_ids else p50_ranges
            if sid in src and aid in src[sid]:
                out[sid][aid] = float(src[sid][aid])
    return out


def effective_dtag_values(
    config,
    ranges_by_id: dict[str, dict[int, float]],
    tag_truth: dict[str, np.ndarray],
    ids: list[str] | set[str] | None = None,
    anchors: set[int] | None = None,
) -> np.ndarray:
    ids_set = set(ids) if ids is not None else None
    vals = []
    for sid, by_anchor in ranges_by_id.items():
        if ids_set is not None and sid not in ids_set:
            continue
        truth = tag_truth.get(sid)
        if truth is None:
            continue
        for aid, measured in by_anchor.items():
            aid_i = int(aid)
            if anchors is not None and aid_i not in anchors:
                continue
            geom = float(np.linalg.norm(np.asarray(config.coords[aid_i], dtype=float) - truth))
            vals.append(float(measured) - geom - float(config.delays[aid_i]))
    return finite(vals)


def calibrate_dtag(config, ranges_by_id: dict[str, dict[int, float]], tag_truth: dict[str, np.ndarray], train_ids: list[str] | set[str]) -> float:
    vals = effective_dtag_values(config, ranges_by_id, tag_truth, ids=train_ids)
    return float(np.nanmedian(vals)) if vals.size else float("nan")


def residual_rows_for_ranges(
    config,
    ranges_by_id: dict[str, dict[int, float]],
    tag_truth: dict[str, np.ndarray],
    d_tag_mm: float,
) -> list[dict[str, Any]]:
    rows = []
    for sid, by_anchor in ranges_by_id.items():
        truth = tag_truth.get(sid)
        if truth is None:
            continue
        for aid, measured in by_anchor.items():
            aid_i = int(aid)
            geom = float(np.linalg.norm(np.asarray(config.coords[aid_i], dtype=float) - truth))
            rho = float(measured) - geom - float(config.delays[aid_i]) - float(d_tag_mm)
            rows.append({"position_id": sid, "anchor_id": aid_i, "anchor_label": ANCHORS[aid_i], "rho_mm": rho})
    return rows


def inverse_rms_sigma(
    config,
    ranges_by_id: dict[str, dict[int, float]],
    tag_truth: dict[str, np.ndarray],
    base_sigma_by_id: dict[int, float],
    d_tag_mm: float = LOO_DTAG_MM,
) -> tuple[dict[int, float], dict[int, float], dict[int, float]]:
    residuals = residual_rows_for_ranges(config, ranges_by_id, tag_truth, d_tag_mm)
    df = pd.DataFrame(residuals)
    rms_by_anchor: dict[int, float] = {}
    raw_weight: dict[int, float] = {}
    for aid in range(8):
        vals = df[df["anchor_id"] == aid]["rho_mm"].to_numpy(dtype=float) if not df.empty else np.empty(0)
        r = rmse(vals)
        if not np.isfinite(r) or r <= 0:
            r = 1.0
        rms_by_anchor[aid] = r
        raw_weight[aid] = 1.0 / r
    scale = 8.0 / sum(raw_weight.values())
    weights = {aid: raw_weight[aid] * scale for aid in range(8)}
    sigma = {
        aid: float(base_sigma_by_id.get(aid, 50.0) / math.sqrt(max(weights[aid], 1e-6)))
        for aid in range(8)
    }
    return sigma, weights, rms_by_anchor


def build_solver(config, sigma_by_id: dict[int, float], d_tag_mm: float, tag: str = STATIC_TAG):
    _ext, _full, ab = get_existing_modules()
    layout = ab.build_layout(
        name=f"followup_{config.label}_{tag}_{d_tag_mm:.3f}",
        labels=list(ANCHORS),
        coords_opti_frame=np.asarray(config.coords, dtype=float),
        delays={int(k): float(v) for k, v in config.delays.items()},
        tag_delay_mm=0.0,
        sigma_by_id={int(k): float(v) for k, v in sigma_by_id.items()},
        metadata={"followup_validation": True},
    )
    solver = ab.TagPositionSolver(layout, ab.SolverConfig(method="T4"), tag_delay_by_tag={tag: float(d_tag_mm)})
    return solver


def solve_ranges(
    config,
    ranges_by_id: dict[str, dict[int, float]],
    ids: list[str],
    tag_truth: dict[str, np.ndarray],
    sigma_by_id: dict[int, float],
    d_tag_mm: float | None = None,
    dtag_by_id: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ext, _full, _ab = get_existing_modules()
    rows: list[dict[str, Any]] = []
    solver_cache: dict[float, Any] = {}
    for sid in ids:
        by_anchor = ranges_by_id.get(sid, {})
        truth = tag_truth.get(sid)
        if truth is None or len(by_anchor) < 4:
            continue
        dtag = float(dtag_by_id[sid]) if dtag_by_id is not None else float(d_tag_mm)
        if not np.isfinite(dtag):
            continue
        key = round(dtag, 9)
        solver = solver_cache.get(key)
        if solver is None:
            solver = build_solver(config, sigma_by_id, dtag)
            solver_cache[key] = solver
        obs = [
            ext.Observation(anchor_id=int(aid), range_mm=float(rng), quality_percent=100.0, status="O")
            for aid, rng in sorted(by_anchor.items())
            if np.isfinite(float(rng)) and float(rng) > 0.0
        ]
        if len(obs) < 4:
            continue
        frame = ext.Frame(tag=STATIC_TAG, sweep=0, host_elapsed_s=0.0, host_epoch_s=0.0, observations=tuple(obs), imu=None)
        result = solver.solve_frame(frame)
        if result is None or getattr(result, "status", "") != "ok":
            continue
        solved = np.asarray([result.x_mm, result.y_mm, result.z_mm], dtype=float)
        diff = solved - truth
        rows.append(
            {
                "position_id": sid,
                "d_tag_mm": dtag,
                "x_mm": float(solved[0]),
                "y_mm": float(solved[1]),
                "z_mm": float(solved[2]),
                "truth_x_mm": float(truth[0]),
                "truth_y_vertical_mm": float(truth[1]),
                "truth_z_mm": float(truth[2]),
                "err_3d_mm": float(np.linalg.norm(diff)),
                "err_horiz_mm": float(math.sqrt(diff[0] * diff[0] + diff[2] * diff[2])),
                "err_vertical_y_mm": float(abs(diff[1])),
                "err_y_vertical_mm": float(diff[1]),
                "n_anchors": int(len(obs)),
            }
        )
    return rows, summarize_static_rows(rows, expected=len(ids))


def summarize_static_rows(rows: list[dict[str, Any]], expected: int) -> dict[str, Any]:
    df = pd.DataFrame(rows)
    if df.empty:
        return {
            "median_3d_mm": float("nan"),
            "p95_3d_mm": float("nan"),
            "rmse_3d_mm": float("nan"),
            "median_vert_mm": float("nan"),
            "signed_vertical_slope_mm_per_m": float("nan"),
            "signed_vertical_slope_r2": float("nan"),
            "n_positions": 0,
            "fail_rate": 1.0,
        }
    slope, r2 = linreg_slope_r2(df["truth_y_vertical_mm"], df["err_y_vertical_mm"], slope_scale=1000.0)
    return {
        "median_3d_mm": pct(df["err_3d_mm"], 50),
        "p95_3d_mm": pct(df["err_3d_mm"], 95),
        "rmse_3d_mm": rmse(df["err_3d_mm"]),
        "median_vert_mm": pct(df["err_vertical_y_mm"], 50),
        "signed_vertical_slope_mm_per_m": slope,
        "signed_vertical_slope_r2": r2,
        "n_positions": int(len(df)),
        "fail_rate": float(max(0, expected - len(df)) / expected),
    }


def sweep_dtag(
    config,
    ranges_by_id: dict[str, dict[int, float]],
    ids: list[str],
    tag_truth: dict[str, np.ndarray],
    sigma_by_id: dict[int, float],
    grid: np.ndarray | None = None,
) -> tuple[float, dict[str, Any], list[dict[str, Any]]]:
    if grid is None:
        grid = np.arange(0.0, 120.0 + 1e-9, 2.0)
    detail = []
    best_d = float("nan")
    best_s: dict[str, Any] = {}
    best_val = float("inf")
    for dtag in grid:
        _rows, summary = solve_ranges(config, ranges_by_id, ids, tag_truth, sigma_by_id, d_tag_mm=float(dtag))
        row = {"d_tag_mm": float(dtag), **summary}
        detail.append(row)
        med = float(summary["median_3d_mm"])
        if np.isfinite(med) and med < best_val:
            best_val = med
            best_d = float(dtag)
            best_s = summary
    return best_d, best_s, detail


def loo_dtag_by_position(config, ranges_by_id, ids: list[str], tag_truth) -> tuple[dict[str, float], list[dict[str, Any]]]:
    by_id = {}
    rows = []
    for held in ids:
        train = [sid for sid in ids if sid != held]
        dtag = calibrate_dtag(config, ranges_by_id, tag_truth, train)
        by_id[held] = dtag
        rows.append(
            {
                "config": config.label,
                "held_out_position": held,
                "n_train_positions": len(train),
                "d_tag_p30_train_median_mm": dtag,
            }
        )
    return by_id, rows


def loo_eval(config, ranges_by_id, ids: list[str], tag_truth, sigma_by_id) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    dtag_by_id, dtag_rows = loo_dtag_by_position(config, ranges_by_id, ids, tag_truth)
    rows, summary = solve_ranges(config, ranges_by_id, ids, tag_truth, sigma_by_id, dtag_by_id=dtag_by_id)
    dvals = finite(list(dtag_by_id.values()))
    summary = dict(summary)
    summary["d_tag_value_mm"] = float(np.nanmean(dvals)) if dvals.size else float("nan")
    summary["d_tag_median_mm"] = float(np.nanmedian(dvals)) if dvals.size else float("nan")
    return rows, summary, dtag_rows


def metric_row(label: str, config_label: str, range_agg: str, weighting: str, dtag_mode: str, dtag_value: float, summary: dict[str, Any], notes: str) -> dict[str, Any]:
    return {
        "label": label,
        "config": config_label,
        "range_aggregation": range_agg,
        "weighting": weighting,
        "dtag_mode": dtag_mode,
        "d_tag_used_mm": dtag_value,
        "median_3d_mm": summary.get("median_3d_mm", float("nan")),
        "p95_3d_mm": summary.get("p95_3d_mm", float("nan")),
        "rmse_3d_mm": summary.get("rmse_3d_mm", float("nan")),
        "median_vert_mm": summary.get("median_vert_mm", float("nan")),
        "signed_vertical_slope_mm_per_m": summary.get("signed_vertical_slope_mm_per_m", float("nan")),
        "n_positions": summary.get("n_positions", 0),
        "fail_rate": summary.get("fail_rate", float("nan")),
        "notes": notes,
    }


def task_f1(ctx_data: dict[str, Any], p50_ranges, p30_ranges) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    ctx = phase_context("F1")
    configs = ctx_data["configs"]
    inputs = ctx_data["inputs"]
    ids = ctx_data["ids"]
    truth = inputs["tag_truth_np"]
    base_sigma = {int(k): float(v) for k, v in inputs["sigma_by_id"].items()}

    v5 = configs["V5_CV5"]
    v4 = configs["V4_CV4"]
    vicon = configs["Vicon_Ccm"]
    sigma_v5_inv, weights_v5, rms_v5 = inverse_rms_sigma(v5, p50_ranges, truth, base_sigma, LOO_DTAG_MM)
    sigma_v4_inv, _weights_v4, _rms_v4 = inverse_rms_sigma(v4, p50_ranges, truth, base_sigma, LOO_DTAG_MM)
    sigma_vicon_inv, _weights_vicon, _rms_vicon = inverse_rms_sigma(vicon, p50_ranges, truth, base_sigma, LOO_DTAG_MM)

    dtag_by_id, dtag_rows = loo_dtag_by_position(v5, p30_ranges, ids, truth)
    dvals = finite(list(dtag_by_id.values()))
    dtag_rows.append(
        {
            "config": "V5_CV5",
            "held_out_position": "ALL_MEAN",
            "n_train_positions": 23,
            "d_tag_p30_train_median_mm": float(np.nanmean(dvals)) if dvals.size else float("nan"),
        }
    )
    dtag_rows.append(
        {
            "config": "V5_CV5",
            "held_out_position": "ALL_MEDIAN",
            "n_train_positions": 23,
            "d_tag_p30_train_median_mm": float(np.nanmedian(dvals)) if dvals.size else float("nan"),
        }
    )
    write_csv(TABLES / "f1_dtag_p30_loo.csv", dtag_rows)

    rows: list[dict[str, Any]] = []
    grid_specs = [
        ("V5_p50_uniform_DLOO", v5, p50_ranges, base_sigma, "p50", "uniform", LOO_DTAG_MM, "fixed", "baseline synthetic p50"),
        ("V5_p30_uniform_DLOO", v5, p30_ranges, base_sigma, "p30", "uniform", LOO_DTAG_MM, "fixed", "p30 only"),
        ("V5_p50_invRMS_DLOO", v5, p50_ranges, sigma_v5_inv, "p50", "inverse_rms", LOO_DTAG_MM, "fixed", "weighting only"),
        ("V5_p30_invRMS_DLOO", v5, p30_ranges, sigma_v5_inv, "p30", "inverse_rms", LOO_DTAG_MM, "fixed", "p30 plus inverse-rms"),
        ("V4_p30_uniform_DLOO", v4, p30_ranges, base_sigma, "p30", "uniform", LOO_DTAG_MM, "fixed", "V4 with V5 LOO tag delay"),
        ("V4_p30_invRMS_DLOO", v4, p30_ranges, sigma_v4_inv, "p30", "inverse_rms", LOO_DTAG_MM, "fixed", "V4 p30 plus inverse-rms"),
        ("Vicon_p30_invRMS_DLOO", vicon, p30_ranges, sigma_vicon_inv, "p30", "inverse_rms", LOO_DTAG_MM, "fixed", "known-anchor p30 plus inverse-rms"),
    ]
    for label, cfg, ranges, sigma, agg, weighting, dtag, mode, notes in grid_specs:
        _solved, summary = solve_ranges(cfg, ranges, ids, truth, sigma, d_tag_mm=dtag)
        rows.append(metric_row(label, cfg.label, agg, weighting, mode, dtag, summary, notes))
        sample_cpu(ctx)

    for label, sigma, weighting in [
        ("V5_p30_uniform_Dsweep", base_sigma, "uniform"),
        ("V5_p30_invRMS_Dsweep", sigma_v5_inv, "inverse_rms"),
    ]:
        best_d, best_s, _detail = sweep_dtag(v5, p30_ranges, ids, truth, sigma)
        rows.append(metric_row(label, v5.label, "p30", weighting, "in_sample_sweep", best_d, best_s, "diagnostic only; full 24-position in-sample D_tag sweep"))
        sample_cpu(ctx)

    for label, sigma, weighting in [
        ("V5_p30_uniform_DLOO_recal", base_sigma, "uniform"),
        ("V5_p30_invRMS_DLOO_recal", sigma_v5_inv, "inverse_rms"),
    ]:
        _solved, summary = solve_ranges(v5, p30_ranges, ids, truth, sigma, dtag_by_id=dtag_by_id)
        rows.append(metric_row(label, v5.label, "p30", weighting, "LOO_recalibrated_from_p30_range_residuals", summary.get("d_tag_value_mm", float(np.nanmean(dvals))), summary, "deployability caveat: LOO-CV on same 24-position campaign"))
        sample_cpu(ctx)

    write_csv(TABLES / "f1_combination_grid.csv", rows)
    lines = ["# Task F1 - p30 + inverse-RMS Combination\n\n"]
    append_md_table(lines, rows, ["label", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "d_tag_used_mm", "notes"])
    best = min([r for r in rows if np.isfinite(float(r["median_3d_mm"]))], key=lambda r: float(r["median_3d_mm"]))
    lines.append(f"Best row by median 3D: `{best['label']}` = {best['median_3d_mm']:.3f} mm.\n")
    (REPORTS / "TASK_F1_COMBINATION.md").write_text("".join(lines), encoding="utf-8")

    f1_extra = {
        "sigma_v5_inv": sigma_v5_inv,
        "weights_v5": weights_v5,
        "rms_v5": rms_v5,
        "dtag_p30_by_id": dtag_by_id,
        "dtag_p30_mean": float(np.nanmean(dvals)) if dvals.size else float("nan"),
        "dtag_p30_median": float(np.nanmedian(dvals)) if dvals.size else float("nan"),
    }
    return finish_phase(ctx), rows, f1_extra


def tier_dtag_map(config, ranges_by_id, tag_truth, cal_ids: list[str], maps: dict[str, dict[str, Any]]) -> dict[str, float]:
    out = {}
    for tier in ("LOW", "MID", "HIGH"):
        ids = [sid for sid in cal_ids if maps["height"].get(sid) == tier]
        out[tier] = calibrate_dtag(config, ranges_by_id, tag_truth, ids)
    return out


def task_f3(ctx_data: dict[str, Any], p50_ranges) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("F3")
    configs = ctx_data["configs"]
    inputs = ctx_data["inputs"]
    maps = ctx_data["maps"]
    ids = ctx_data["ids"]
    truth = inputs["tag_truth_np"]
    base_sigma = {int(k): float(v) for k, v in inputs["sigma_by_id"].items()}
    rng = np.random.default_rng(20260618)
    rows = []
    v4_rows = []
    stability = []

    for cfg_label in ("V5_CV5", "V4_CV4"):
        cfg = configs[cfg_label]
        cal = sorted(ctx_data["ext"].stratified_sample(rng, ids, maps, 6))
        eval_ids = [sid for sid in ids if sid not in set(cal)]
        scalar_dtag = calibrate_dtag(cfg, p50_ranges, truth, cal)
        _solved, scalar_summary = solve_ranges(cfg, p50_ranges, eval_ids, truth, base_sigma, d_tag_mm=scalar_dtag)
        tier_map = tier_dtag_map(cfg, p50_ranges, truth, cal, maps)
        by_id = {sid: tier_map.get(maps["height"].get(sid, ""), float("nan")) for sid in eval_ids}
        _solved, tier_summary = solve_ranges(cfg, p50_ranges, eval_ids, truth, base_sigma, dtag_by_id=by_id)
        target = rows if cfg_label == "V5_CV5" else v4_rows
        target.append(metric_row(f"{cfg_label}_stratified_scalar", cfg_label, "p50", "uniform", "single_scalar_from_disjoint_stratified_cal", scalar_dtag, scalar_summary, f"cal={';'.join(cal)} eval_complement=true"))
        target.append(metric_row(f"{cfg_label}_stratified_per_tier", cfg_label, "p50", "uniform", "three_tier_values_from_disjoint_stratified_cal", float("nan"), tier_summary, f"LOW={tier_map['LOW']:.3f}; MID={tier_map['MID']:.3f}; HIGH={tier_map['HIGH']:.3f}; cal={';'.join(cal)}"))
        sample_cpu(ctx)

    rng = np.random.default_rng(777)
    for cfg_label in ("V5_CV5", "V4_CV4"):
        cfg = configs[cfg_label]
        for split_id in range(100):
            cal = sorted(ctx_data["ext"].stratified_sample(rng, ids, maps, 6))
            eval_ids = [sid for sid in ids if sid not in set(cal)]
            dtag = calibrate_dtag(cfg, p50_ranges, truth, cal)
            _solved, summary = solve_ranges(cfg, p50_ranges, eval_ids, truth, base_sigma, d_tag_mm=dtag)
            stability.append(
                {
                    "config": cfg_label,
                    "split_id": split_id,
                    "d_tag_mm": dtag,
                    "n_cal": len(cal),
                    "n_eval": len(eval_ids),
                    "cal_positions": ";".join(cal),
                    **summary,
                }
            )
        sample_cpu(ctx)

    stab_df = pd.DataFrame(stability)
    summary_rows = []
    for cfg_label, g in stab_df.groupby("config"):
        vals = g["median_3d_mm"].to_numpy(dtype=float)
        dvals = g["d_tag_mm"].to_numpy(dtype=float)
        summary_rows.append(
            {
                "config": cfg_label,
                "n_splits": int(len(g)),
                "d_tag_mean_mm": pct(dvals, 50),
                "mean_median_3d_mm": float(np.nanmean(vals)),
                "std_median_3d_mm": float(np.nanstd(vals, ddof=1)),
                "min_median_3d_mm": pct(vals, 0),
                "max_median_3d_mm": pct(vals, 100),
                "p5_median_3d_mm": pct(vals, 5),
                "p95_median_3d_mm": pct(vals, 95),
            }
        )
    write_csv(TABLES / "f3_stratified_scalar_vs_pertier.csv", rows)
    write_csv(TABLES / "f3_stratified_stability.csv", stability)
    write_csv(TABLES / "f3_stratified_stability_summary.csv", summary_rows)
    write_csv(TABLES / "f3_v4_comparison.csv", v4_rows)
    lines = ["# Task F3 - Stratified LMH Sanity Check\n\n"]
    lines.append("V5 explicit scalar/per-tier check:\n\n")
    append_md_table(lines, rows, ["label", "dtag_mode", "d_tag_used_mm", "median_3d_mm", "rmse_3d_mm", "notes"])
    lines.append("Stability across 100 random stratified scalar splits:\n\n")
    append_md_table(lines, summary_rows, ["config", "mean_median_3d_mm", "std_median_3d_mm", "min_median_3d_mm", "p95_median_3d_mm"])
    (REPORTS / "TASK_F3_STRATIFIED_SANITY.md").write_text("".join(lines), encoding="utf-8")
    return finish_phase(ctx), rows, stability, summary_rows


def task_f4(ctx_data: dict[str, Any], percentile_maps: dict[int, dict[str, dict[int, float]]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("F4")
    configs = ctx_data["configs"]
    inputs = ctx_data["inputs"]
    ids = ctx_data["ids"]
    truth = inputs["tag_truth_np"]
    base_sigma = {int(k): float(v) for k, v in inputs["sigma_by_id"].items()}
    recal_rows = []
    fixed_rows = []
    for cfg_label in ("V5_CV5", "V4_CV4"):
        cfg = configs[cfg_label]
        for percentile_value in PERCENTILES:
            ranges = percentile_maps[int(percentile_value)]
            _rows, fixed = solve_ranges(cfg, ranges, ids, truth, base_sigma, d_tag_mm=LOO_DTAG_MM)
            fixed_rows.append(
                {
                    "config": cfg_label,
                    "percentile": percentile_value,
                    "d_tag_fixed_mm": LOO_DTAG_MM,
                    **fixed,
                }
            )
            _rows, loo_summary, dtag_rows = loo_eval(cfg, ranges, ids, truth, base_sigma)
            dvals = finite([r["d_tag_p30_train_median_mm"] for r in dtag_rows])
            recal_rows.append(
                {
                    "config": cfg_label,
                    "percentile": percentile_value,
                    "d_tag_recal_mm": float(np.nanmean(dvals)) if dvals.size else float("nan"),
                    "d_tag_recal_median_mm": float(np.nanmedian(dvals)) if dvals.size else float("nan"),
                    "loo_median_3d_mm": loo_summary["median_3d_mm"],
                    "loo_p95_3d_mm": loo_summary["p95_3d_mm"],
                    "loo_rmse_3d_mm": loo_summary["rmse_3d_mm"],
                    "n_positions": loo_summary["n_positions"],
                    "fail_rate": loo_summary["fail_rate"],
                }
            )
            sample_cpu(ctx)
    write_csv(TABLES / "f4_percentile_recalibrated.csv", recal_rows)
    write_csv(TABLES / "f4_percentile_fixed_dtag.csv", fixed_rows)
    lines = ["# Task F4 - Fair Percentile Sweep\n\n", "Recalibrated LOO results:\n\n"]
    append_md_table(lines, recal_rows, ["config", "percentile", "d_tag_recal_mm", "loo_median_3d_mm", "loo_p95_3d_mm", "loo_rmse_3d_mm"])
    (REPORTS / "TASK_F4_FAIR_PERCENTILE.md").write_text("".join(lines), encoding="utf-8")
    return finish_phase(ctx), recal_rows, fixed_rows


def task_f5(ctx_data: dict[str, Any], p50_ranges, p30_ranges) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("F5")
    cfg = ctx_data["configs"]["V5_CV5"]
    inputs = ctx_data["inputs"]
    ids = ctx_data["ids"]
    truth = inputs["tag_truth_np"]
    base_sigma = {int(k): float(v) for k, v in inputs["sigma_by_id"].items()}
    r50 = pd.DataFrame(residual_rows_for_ranges(cfg, p50_ranges, truth, LOO_DTAG_MM))
    r30 = pd.DataFrame(residual_rows_for_ranges(cfg, p30_ranges, truth, LOO_DTAG_MM))
    rows = []
    for aid in range(8):
        v50 = r50[r50["anchor_id"] == aid]["rho_mm"].to_numpy(dtype=float)
        v30 = r30[r30["anchor_id"] == aid]["rho_mm"].to_numpy(dtype=float)
        rows.append(
            {
                "anchor_label": ANCHORS[aid],
                "anchor_id": aid,
                "layer": "lower" if aid < 4 else "upper",
                "median_abs_rho_p30": pct(np.abs(v30), 50),
                "median_abs_rho_p50": pct(np.abs(v50), 50),
                "improvement_mm": pct(np.abs(v30), 50) - pct(np.abs(v50), 50),
                "nlos_spike_rate": float(np.mean(v50 > 100.0)) if v50.size else float("nan"),
            }
        )
    selective_df = hybrid_ranges(p50_ranges, p30_ranges, {3, 5})
    strat_rows = []
    for label, ranges in [
        ("global_p50", p50_ranges),
        ("global_p30", p30_ranges),
        ("selective_DF_p30_else_p50", selective_df),
    ]:
        _solved, summary = solve_ranges(cfg, ranges, ids, truth, base_sigma, d_tag_mm=LOO_DTAG_MM)
        strat_rows.append({"strategy": label, "d_tag_mm": LOO_DTAG_MM, **summary})
    write_csv(TABLES / "f5_per_anchor_percentile.csv", rows)
    write_csv(TABLES / "f5_selective_percentile_results.csv", strat_rows)
    lines = ["# Task F5 - Per-anchor Percentile Quality\n\n"]
    append_md_table(lines, rows, ["anchor_label", "median_abs_rho_p30", "median_abs_rho_p50", "improvement_mm", "nlos_spike_rate"])
    append_md_table(lines, strat_rows, ["strategy", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm"])
    (REPORTS / "TASK_F5_PERCENTILE_PER_ANCHOR.md").write_text("".join(lines), encoding="utf-8")
    return finish_phase(ctx), rows, strat_rows


def roto_worker(job: dict[str, Any]) -> dict[str, Any]:
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    ext = load_module(EXT_SCRIPT, f"followup_ext_roto_{os.getpid()}")
    _mech, full = ext.load_previous_modules()
    _ext, _full, ab = ext.worker_context()
    cfg_coords = np.asarray(job["coords"], dtype=float)
    delays = {int(k): float(v) for k, v in job["delays"].items()}
    sigma = {int(k): float(v) for k, v in job["sigma_by_id"].items()}
    layout = ab.build_layout(
        name=f"followup_roto_{job['capture_id']}_{job['tag']}_{job['method']}",
        labels=list(ANCHORS),
        coords_opti_frame=cfg_coords,
        delays=delays,
        tag_delay_mm=0.0,
        sigma_by_id=sigma,
        metadata={"followup_roto": True},
    )
    solver = ab.TagPositionSolver(layout, ab.SolverConfig(method="T4"), tag_delay_by_tag={job["tag"]: float(job["d_tag_mm"])})
    frames = ext.read_tr_all_frames(Path(job["path"]), tags={job["tag"]}, min_anchors=4)
    seq = []
    for frame in frames:
        by_anchor: dict[int, float] = {}
        for obs in frame.observations:
            aid = int(obs.anchor_id)
            if 0 <= aid < 8 and float(obs.range_mm) > 0.0:
                by_anchor[aid] = float(obs.range_mm)
        if len(by_anchor) >= 4:
            seq.append((float(frame.host_elapsed_s), float(frame.host_epoch_s), int(frame.sweep), by_anchor))
    points = []
    method = str(job["method"])
    for idx, (elapsed, epoch, sweep, by_anchor) in enumerate(seq):
        if method == "raw_frame":
            agg = by_anchor
        else:
            m = re.match(r"(p30|median)_win(\d+)", method)
            if not m:
                continue
            kind, win_s = m.groups()
            win = int(win_s)
            start = max(0, idx - win + 1)
            hist = seq[start : idx + 1]
            agg = {}
            for aid in range(8):
                vals = [h[3][aid] for h in hist if aid in h[3]]
                if len(vals) >= max(3, min(5, win // 4)):
                    agg[aid] = float(np.nanpercentile(vals, 30.0)) if kind == "p30" else float(np.nanmedian(vals))
        if len(agg) < 4:
            continue
        obs_tuple = tuple(
            ext.Observation(anchor_id=int(aid), range_mm=float(rng), quality_percent=100.0, status="O")
            for aid, rng in sorted(agg.items())
        )
        frame = ext.Frame(tag=job["tag"], sweep=sweep, host_elapsed_s=elapsed, host_epoch_s=epoch, observations=obs_tuple, imu=None)
        result = solver.solve_frame(frame)
        if result is None or getattr(result, "status", "") != "ok":
            continue
        points.append({"time_s": elapsed, "x_mm": float(result.x_mm), "y_mm": float(result.y_mm), "z_mm": float(result.z_mm)})
    return {
        "capture_id": job["capture_id"],
        "tag": job["tag"],
        "method": method,
        "d_tag_mm": float(job["d_tag_mm"]),
        "frames_input": len(seq),
        "frames_solved": len(points),
        "points": points,
    }


def evaluate_roto_track(track: dict[str, Any], full, opti_by_capture, mapping, offsets) -> dict[str, Any]:
    cid = track["capture_id"]
    tag = track["tag"]
    pts = track["points"]
    marker = mapping.get(tag, "")
    beta = offsets.get(cid, float("nan"))
    status = "ok"
    if not pts or not marker or cid not in opti_by_capture or not math.isfinite(beta):
        return {
            "capture_id": cid,
            "tag": tag,
            "aggregation_method": track["method"],
            "d_tag_mm": track["d_tag_mm"],
            "status": "missing_or_no_points",
            "n_overlap": 0,
            "frames_input": track["frames_input"],
            "frames_solved": track["frames_solved"],
            "median_3d_mm": float("nan"),
            "p95_3d_mm": float("nan"),
            "rmse_3d_mm": float("nan"),
            "alignment": "BEST-FIT-ALIGNED: existing capture-level V4/T4 time offsets reused; no hardware time sync",
        }
    t = np.asarray([p["time_s"] for p in pts], dtype=float)
    xyz = np.asarray([[p["x_mm"], p["y_mm"], p["z_mm"]] for p in pts], dtype=float)
    truth, good = full.interpolate_opti(opti_by_capture[cid][marker], t + beta)
    mask = good & np.isfinite(xyz).all(axis=1) & np.isfinite(truth).all(axis=1)
    diff = xyz[mask] - truth[mask]
    err = np.linalg.norm(diff, axis=1) if diff.size else np.empty(0)
    if not err.size:
        status = "no_overlap"
    return {
        "capture_id": cid,
        "tag": tag,
        "aggregation_method": track["method"],
        "d_tag_mm": track["d_tag_mm"],
        "status": status,
        "n_overlap": int(err.size),
        "frames_input": track["frames_input"],
        "frames_solved": track["frames_solved"],
        "median_3d_mm": pct(err, 50),
        "p95_3d_mm": pct(err, 95),
        "rmse_3d_mm": rmse(err),
        "alignment": "BEST-FIT-ALIGNED: existing capture-level V4/T4 time offsets reused; no hardware time sync",
    }


def task_f2(ctx_data: dict[str, Any], f1_extra: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("F2")
    full = ctx_data["full"]
    configs = ctx_data["configs"]
    inputs = ctx_data["inputs"]
    v5 = configs["V5_CV5"]
    base_sigma = {int(k): float(v) for k, v in inputs["sigma_by_id"].items()}
    methods = [
        ("raw_frame", LOO_DTAG_MM),
        ("p30_win10", LOO_DTAG_MM),
        ("p30_win20", LOO_DTAG_MM),
        ("p30_win50", LOO_DTAG_MM),
        ("median_win20", LOO_DTAG_MM),
        ("p30_win20_recal", f1_extra.get("dtag_p30_mean", LOO_DTAG_MM)),
    ]
    skip_rows = []
    try:
        roto_files = full.discover_roto_files()
        mapping = full.read_mapping(full.MAPPING_PATH)
        offsets = full.read_offsets(full.OFFSETS_PATH)
        opti_by_capture = {
            cid: full.parse_trc_trajectories(full.OPTI_ROOT / f"{cid}.trc", full.ROTO_MARKERS)
            for cid in roto_files
            if (full.OPTI_ROOT / f"{cid}.trc").exists()
        }
    except Exception as exc:
        skip_rows.append({"status": "skipped", "reason": repr(exc)})
        write_csv(TABLES / "f2_roto_p30_results.csv", skip_rows)
        write_csv(TABLES / "f2_roto_p30_summary.csv", skip_rows)
        (REPORTS / "TASK_F2_ROTO_P30.md").write_text(f"# Task F2 - ROTO p30\n\nSkipped: {exc!r}\n", encoding="utf-8")
        return finish_phase(ctx), skip_rows, skip_rows
    jobs = []
    for cid, path in roto_files.items():
        for tag in ROTO_TAGS:
            for method, dtag in methods:
                jobs.append(
                    {
                        "capture_id": cid,
                        "path": str(path),
                        "tag": tag,
                        "method": method,
                        "d_tag_mm": float(dtag),
                        "coords": np.asarray(v5.coords, dtype=float).tolist(),
                        "delays": {int(k): float(val) for k, val in v5.delays.items()},
                        "sigma_by_id": base_sigma,
                    }
                )
    tracks = []
    mp_ctx = get_context("spawn")
    with ProcessPoolExecutor(max_workers=WORKERS, mp_context=mp_ctx) as pool:
        futs = [pool.submit(roto_worker, job) for job in jobs]
        for i, fut in enumerate(as_completed(futs), start=1):
            tracks.append(fut.result())
            if i % 8 == 0 or i == len(futs):
                sample_cpu(ctx)
                print(json.dumps({"stage": "F2_roto", "done": i, "total": len(futs), "live_cpu_percent": ctx["cpu"][-1]}, sort_keys=True), flush=True)
    result_rows = [evaluate_roto_track(tr, full, opti_by_capture, mapping, offsets) for tr in tracks]
    result_rows = sorted(result_rows, key=lambda r: (str(r["aggregation_method"]), str(r["capture_id"]), str(r["tag"])))
    summary = []
    df = pd.DataFrame(result_rows)
    for method, g in df.groupby("aggregation_method", dropna=False):
        summary.append(
            {
                "aggregation_method": method,
                "n_tracks": int(len(g)),
                "n_ok": int((g["status"] == "ok").sum()),
                "median_3d_all_tracks": pct(g["median_3d_mm"], 50),
                "p95_all_tracks": pct(g["p95_3d_mm"], 50),
                "rmse_all_tracks": pct(g["rmse_3d_mm"], 50),
                "notes": "BEST-FIT-ALIGNED; existing capture-level offsets reused",
            }
        )
    if FULL_V5_ROTO_TRACK.exists():
        old = pd.read_csv(FULL_V5_ROTO_TRACK)
        old_sum = old[(old["capture_id"].isna()) & (old["tag_delay_mode"] == "D_LOO_CV")]
        for _, row in old_sum.iterrows():
            summary.append(
                {
                    "aggregation_method": "existing_FULL_V5_p50_DLOO",
                    "n_tracks": int(row.get("n_positions", 0)),
                    "n_ok": int(row.get("n_positions", 0)),
                    "median_3d_all_tracks": float(row.get("median_3d_mm", float("nan"))),
                    "p95_all_tracks": float(row.get("p95_3d_mm", float("nan"))),
                    "rmse_all_tracks": float(row.get("rmse_3d_mm", float("nan"))),
                    "notes": "existing FULL_V5 output; original per-frame pipeline",
                }
            )
    write_csv(TABLES / "f2_roto_p30_results.csv", result_rows)
    write_csv(TABLES / "f2_roto_p30_summary.csv", summary)
    lines = ["# Task F2 - ROTO p30 Transfer\n\n"]
    append_md_table(lines, summary, ["aggregation_method", "median_3d_all_tracks", "p95_all_tracks", "rmse_all_tracks", "notes"])
    (REPORTS / "TASK_F2_ROTO_P30.md").write_text("".join(lines), encoding="utf-8")
    return finish_phase(ctx), result_rows, summary


def task_f6(ctx_data: dict[str, Any], p50_ranges, p30_ranges) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("F6")
    configs = ctx_data["configs"]
    inputs = ctx_data["inputs"]
    ids = ctx_data["ids"]
    truth = inputs["tag_truth_np"]
    base_sigma = {int(k): float(v) for k, v in inputs["sigma_by_id"].items()}
    rows = []
    variants = [
        ("V4 production", configs["V4_CV4"], p50_ranges, "p50", "uniform", "fixed_0", 0.0, base_sigma),
        ("V5 baseline", configs["V5_CV5"], p50_ranges, "p50", "uniform", "fixed_LOO_49.621", LOO_DTAG_MM, base_sigma),
    ]
    for label, cfg, ranges, agg, weighting, dmode, dtag, sigma in variants:
        _solved, summary = solve_ranges(cfg, ranges, ids, truth, sigma, d_tag_mm=dtag)
        rows.append(
            {
                "variant": label,
                "layout": cfg.layout_source,
                "correction": cfg.correction_source,
                "percentile": agg,
                "weighting": weighting,
                "d_tag_mode": dmode,
                "d_tag_value": dtag,
                **summary,
            }
        )
    for label, cfg_key in [
        ("V5 improved", "V5_CV5"),
        ("V4 improved", "V4_CV4"),
        ("Vicon improved", "Vicon_Ccm"),
    ]:
        cfg = configs[cfg_key]
        sigma_inv, _weights, _rms = inverse_rms_sigma(cfg, p50_ranges, truth, base_sigma, LOO_DTAG_MM)
        _solved, summary, _drows = loo_eval(cfg, p30_ranges, ids, truth, sigma_inv)
        rows.append(
            {
                "variant": label,
                "layout": cfg.layout_source,
                "correction": cfg.correction_source,
                "percentile": "p30",
                "weighting": "inverse_rms",
                "d_tag_mode": "LOO_recalibrated_from_p30_range_residuals",
                "d_tag_value": summary.get("d_tag_value_mm", float("nan")),
                **summary,
            }
        )
        sample_cpu(ctx)
    write_csv(TABLES / "f6_final_comparison.csv", rows)
    lines = ["# Task F6 - Best-practice Headline\n\n"]
    append_md_table(lines, rows, ["variant", "percentile", "weighting", "d_tag_mode", "d_tag_value", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_vert_mm", "signed_vertical_slope_mm_per_m"])
    (REPORTS / "TASK_F6_BEST_PRACTICE.md").write_text("".join(lines), encoding="utf-8")
    return finish_phase(ctx), rows


def summarize_all(task_outputs: dict[str, Any], runtimes: list[dict[str, Any]]) -> None:
    f1_rows = task_outputs.get("F1", [])
    f2_summary = task_outputs.get("F2_summary", [])
    f3_summary = task_outputs.get("F3_summary", [])
    f4_rows = task_outputs.get("F4_recal", [])
    f5_results = task_outputs.get("F5_results", [])
    f6_rows = task_outputs.get("F6", [])

    def best(rows: list[dict[str, Any]], key: str = "median_3d_mm"):
        finite_rows = [r for r in rows if key in r and np.isfinite(float(r[key]))]
        return min(finite_rows, key=lambda r: float(r[key])) if finite_rows else {}

    f1_combo = next((r for r in f1_rows if r.get("label") == "V5_p30_invRMS_DLOO_recal"), {})
    f2_window = [
        r
        for r in f2_summary
        if str(r.get("aggregation_method", "")) not in {"raw_frame", "existing_FULL_V5_p50_DLOO"}
    ]
    f2_best_window = best(f2_window, "median_3d_all_tracks")
    f2_raw = next((r for r in f2_summary if r.get("aggregation_method") == "raw_frame"), {})
    f4_v5 = [r for r in f4_rows if r.get("config") == "V5_CV5"]
    f4_best = best([{**r, "median_3d_mm": r.get("loo_median_3d_mm", float("nan"))} for r in f4_v5])
    f4_p30 = next((r for r in f4_v5 if int(r.get("percentile", -1)) == 30), {})
    f5_best = best(f5_results)
    f6_best = best(f6_rows)
    f6_v5 = next((r for r in f6_rows if r.get("variant") == "V5 improved"), {})
    f3_v5 = [r for r in f3_summary if r.get("config") == "V5_CV5"]
    f3_txt = "not available"
    if f3_v5:
        f3_txt = f"scalar stratified mean median {float(f3_v5[0]['mean_median_3d_mm']):.1f} mm, std {float(f3_v5[0]['std_median_3d_mm']):.1f} mm"

    synthesis = [
        {"Task": "F1", "Key Finding": f"p30+inverse-RMS+recal = {float(f1_combo.get('median_3d_mm', float('nan'))):.1f} mm; it does not break 45 mm"},
        {"Task": "F2", "Key Finding": f"ROTO p30 does not transfer: best p30/median window = {float(f2_best_window.get('median_3d_all_tracks', float('nan'))):.1f} mm vs raw/p50 {float(f2_raw.get('median_3d_all_tracks', float('nan'))):.1f} mm"},
        {"Task": "F3", "Key Finding": f3_txt},
        {"Task": "F4", "Key Finding": f"fair LOO recalibration shifts V5 optimum to p{f4_best.get('percentile', 'n/a')} = {float(f4_best.get('loo_median_3d_mm', float('nan'))):.1f} mm; p30 = {float(f4_p30.get('loo_median_3d_mm', float('nan'))):.1f} mm"},
        {"Task": "F5", "Key Finding": f"{f5_best.get('strategy', 'n/a')} = {float(f5_best.get('median_3d_mm', float('nan'))):.1f} mm"},
        {"Task": "F6", "Key Finding": f"specified headline variants: V5 improved = {float(f6_v5.get('median_3d_mm', float('nan'))):.1f} mm; best row is {f6_best.get('variant', 'n/a')} = {float(f6_best.get('median_3d_mm', float('nan'))):.1f} mm"},
    ]
    lines = ["# Follow-up Validation Summary\n\n", f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n"]
    append_md_table(lines, synthesis, ["Task", "Key Finding"])
    lines.append("## Final Headline Table\n\n")
    append_md_table(lines, f6_rows, ["variant", "percentile", "weighting", "d_tag_mode", "d_tag_value", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm"])
    lines.append("## Runtime\n\n")
    append_md_table(lines, runtimes, ["task", "elapsed_s", "mean_cpu_percent", "max_cpu_percent", "workers", "physical_cores", "logical_cores"])
    (REPORTS / "FOLLOWUP_VALIDATION_SUMMARY.md").write_text("".join(lines), encoding="utf-8")


def verify_outputs() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(TABLES.glob("*.csv")):
        try:
            with path.open(newline="", encoding="utf-8") as f:
                n = max(0, sum(1 for _ in f) - 1)
        except Exception:
            n = -1
        rows.append({"csv": str(path.relative_to(OUT_ROOT)), "row_count": n})
    write_csv(TABLES / "output_row_counts.csv", rows)
    source = THIS.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(THIS))
    blocked_roots = {"torch", "cupy", "cuda"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in blocked_roots:
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in blocked_roots:
                bad.append(node.module)
    compile(source, str(THIS), "exec")
    write_csv(
        TABLES / "verification.csv",
        [
            {
                "script_compiles": True,
                "forbidden_gpu_imports_found": ";".join(bad),
                "gpu_import_check_passed": not bad,
                "workers": WORKERS,
            }
        ],
    )
    print("=== OUTPUT CSV ROW COUNTS ===")
    for row in rows:
        print(f"{row['csv']}: {row['row_count']}")
    print(f"Script compiles: yes")
    print(f"Forbidden GPU imports found: {'none' if not bad else '; '.join(bad)}")
    return rows


def main() -> int:
    for path in (OUT_ROOT, TABLES, REPORTS, SCRIPTS):
        path.mkdir(parents=True, exist_ok=True)
    for path, label in [
        (EXT_SCRIPT, "extended mechanism ablation script"),
        (FULL_V5_SCRIPT, "FULL_V5 script"),
    ]:
        require_path(path, label)
    total_start = time.perf_counter()
    ctx_data = build_context()
    raw_ranges = ctx_data["raw_ranges"]
    p50_ranges = percentile_ranges(raw_ranges, 50)
    p30_ranges = percentile_ranges(raw_ranges, 30)
    percentile_maps = {int(p): percentile_ranges(raw_ranges, p) for p in PERCENTILES}
    runtimes: list[dict[str, Any]] = []
    outputs: dict[str, Any] = {}

    rt, f1_rows, f1_extra = task_f1(ctx_data, p50_ranges, p30_ranges)
    runtimes.append(rt)
    outputs["F1"] = f1_rows

    rt, f4_recal, f4_fixed = task_f4(ctx_data, percentile_maps)
    runtimes.append(rt)
    outputs["F4_recal"] = f4_recal
    outputs["F4_fixed"] = f4_fixed

    rt, f3_rows, f3_stability, f3_summary = task_f3(ctx_data, p50_ranges)
    runtimes.append(rt)
    outputs["F3"] = f3_rows
    outputs["F3_stability"] = f3_stability
    outputs["F3_summary"] = f3_summary

    rt, f5_rows, f5_results = task_f5(ctx_data, p50_ranges, p30_ranges)
    runtimes.append(rt)
    outputs["F5"] = f5_rows
    outputs["F5_results"] = f5_results

    rt, f2_rows, f2_summary = task_f2(ctx_data, f1_extra)
    runtimes.append(rt)
    outputs["F2"] = f2_rows
    outputs["F2_summary"] = f2_summary

    rt, f6_rows = task_f6(ctx_data, p50_ranges, p30_ranges)
    runtimes.append(rt)
    outputs["F6"] = f6_rows

    summarize_all(outputs, runtimes)
    verify_outputs()

    total = time.perf_counter() - total_start
    mean_cpu = float(np.nanmean([r["mean_cpu_percent"] for r in runtimes])) if runtimes else float("nan")
    max_cpu = float(np.nanmax([r["max_cpu_percent"] for r in runtimes])) if runtimes else float("nan")
    print("\n=== POST-GPU FOLLOW-UP VALIDATION — RUNTIME SUMMARY ===")
    print("Machine: i7-8700K 6C/12T 32GB")
    print(f"Workers: {WORKERS} (process pool for ROTO), GPU idle")
    for r in runtimes:
        print(f"{r['task']}: {r['elapsed_s']:.1f} s, mean CPU {r['mean_cpu_percent']:.1f}%, max CPU {r['max_cpu_percent']:.1f}%")
    print(f"Total wall time: {total:.1f} s")
    print(f"Mean CPU%: {mean_cpu:.1f}%")
    print(f"Max CPU%: {max_cpu:.1f}%")
    best_final = min([r for r in f6_rows if np.isfinite(float(r["median_3d_mm"]))], key=lambda r: float(r["median_3d_mm"]))
    print("\n=== FOLLOW-UP VALIDATION KEY RESULT ===")
    print(f"Best F6 row: {best_final['variant']} = {best_final['median_3d_mm']:.3f} mm median 3D")
    print(f"Summary: {REPORTS / 'FOLLOWUP_VALIDATION_SUMMARY.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
