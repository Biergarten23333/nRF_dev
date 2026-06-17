#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import numpy as np
import pandas as pd
import psutil
from scipy import stats


THIS = Path(__file__).resolve()
ANALYSIS = THIS.parents[2]
BASE = THIS.parents[4]
OUT_ROOT = ANALYSIS / "FULL_V5_mechanism_ablations"
FULL_V5_SCRIPT = ANALYSIS / "FULL_V5/scripts/run_full_v5_ablation_pipeline.py"
TRANSFER_SWEEP = ANALYSIS / "FULL_transfer_matrix/tables/transfer_matrix_Dsweep_detail.csv"
TRANSFER_CELLS = ANALYSIS / "FULL_transfer_matrix/tables/transfer_matrix_48cells.csv"
VICON_CM_DELAY = ANALYSIS / "FULL_V5_align_to_Vicon/tables/vicon_anchor_delays_refit_cm.csv"
B_SWEEP = ANALYSIS / "FULL_4way_comparison/tables/B_dtag_sweep_curve.csv"
WORKERS = 6
LOO_DTAG_MM = 49.621

ANCHORS = tuple("ABCDEFGH")

_WORKER_FULL = None
_WORKER_ABLATION = None


@dataclass(frozen=True)
class ConfigSpec:
    config: str
    layout_source: str
    correction_source: str
    coords: np.ndarray
    delays: dict[int, float]
    full_loo_median_mm: float
    full_loo_p95_mm: float
    full_loo_rmse_mm: float


def require_path(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"required input missing: {label}: {path}")
    return path


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_full_module():
    return load_module(FULL_V5_SCRIPT, "full_v5_pipeline_helpers_mechanism")


def worker_context():
    global _WORKER_FULL, _WORKER_ABLATION
    if _WORKER_FULL is None:
        _WORKER_FULL = load_module(FULL_V5_SCRIPT, f"full_v5_pipeline_helpers_worker_{os.getpid()}")
    if _WORKER_ABLATION is None:
        _WORKER_ABLATION = _WORKER_FULL.load_module(
            _WORKER_FULL.ABLATION_SCRIPT,
            f"static_ablation_mechanism_worker_{os.getpid()}",
        )
    return _WORKER_FULL, _WORKER_ABLATION


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
                vals.append("" if not np.isfinite(val) else f"{float(val):.3f}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |\n")
    if max_rows is not None and len(rows) > max_rows:
        lines.append(f"\n... {len(rows) - max_rows} rows omitted ...\n")
    lines.append("\n")


def make_dirs() -> dict[str, Path]:
    dirs = {
        "root": OUT_ROOT,
        "scripts": OUT_ROOT / "scripts",
        "reports": OUT_ROOT / "reports",
        "A": OUT_ROOT / "A_hard_cv/tables",
        "B": OUT_ROOT / "B_residual_field/tables",
        "C": OUT_ROOT / "C_cancellation_valley/tables",
        "D": OUT_ROOT / "D_per_height_dtag/tables",
        "E": OUT_ROOT / "E_dtag_curves/tables",
        "F": OUT_ROOT / "F_multi_criterion_dtag/tables",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def phase_context(item: str) -> dict[str, Any]:
    psutil.cpu_percent(interval=None)
    return {
        "item": item,
        "physical_cores": psutil.cpu_count(logical=False) or 0,
        "logical_cores": psutil.cpu_count(logical=True) or os.cpu_count() or 0,
        "workers": WORKERS,
        "start": time.perf_counter(),
        "cpu_samples": [],
    }


def sample_cpu(ctx: dict[str, Any]) -> float:
    val = float(psutil.cpu_percent(interval=0.0))
    ctx["cpu_samples"].append(val)
    return val


def finish_phase(ctx: dict[str, Any]) -> dict[str, Any]:
    samples = ctx["cpu_samples"] or [sample_cpu(ctx)]
    return {
        "item": ctx["item"],
        "physical_cores": ctx["physical_cores"],
        "logical_cores": ctx["logical_cores"],
        "workers": ctx["workers"],
        "elapsed_s": time.perf_counter() - ctx["start"],
        "mean_cpu_percent": float(np.nanmean(samples)),
        "max_cpu_percent": float(np.nanmax(samples)),
    }


def finite(values: Any) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    return arr[np.isfinite(arr)]


def percentile(values: Any, pct: float) -> float:
    arr = finite(values)
    return float(np.nanpercentile(arr, pct)) if arr.size else float("nan")


def rmse(values: Any) -> float:
    arr = finite(values)
    return float(math.sqrt(float(np.nanmean(arr * arr)))) if arr.size else float("nan")


def regress_mm_per_m(x_mm: Any, y_mm: Any) -> dict[str, float]:
    x = np.asarray(x_mm, dtype=float)
    y = np.asarray(y_mm, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    if x.size < 3 or float(np.nanstd(x)) <= 1e-12:
        return {"slope": float("nan"), "intercept": float("nan"), "r2": float("nan"), "p_value": float("nan")}
    res = stats.linregress(x, y)
    return {
        "slope": float(res.slope * 1000.0),
        "intercept": float(res.intercept),
        "r2": float(res.rvalue * res.rvalue),
        "p_value": float(res.pvalue),
    }


def static_id_from_path(path: str | Path) -> str:
    p = Path(path)
    for parent in p.parents:
        if parent.name.startswith("static_ID"):
            return parent.name.split("_")[1]
    raise ValueError(f"cannot parse static ID from {p}")


def solve_static_job(job: dict[str, Any]) -> dict[str, Any]:
    full, ab = worker_context()
    coords = np.asarray(job["coords"], dtype=float)
    delays = {int(k): float(v) for k, v in job["delays"].items()}
    layout = ab.build_layout(
        name=str(job["layout_name"]),
        labels=list(ANCHORS),
        coords_opti_frame=coords,
        delays=delays,
        tag_delay_mm=0.0,
        sigma_by_id={int(k): float(v) for k, v in job["sigma_by_id"].items()},
        metadata=job.get("metadata", {}),
    )
    solver = ab.TagPositionSolver(
        layout,
        ab.SolverConfig(method=str(job.get("method", "T4"))),
        tag_delay_by_tag={full.STATIC_TAG: float(job["d_tag_mm"])},
    )
    tag_truth = {k: np.asarray(v, dtype=float) for k, v in job["tag_truth"].items()}
    anchor_centroid = np.asarray(job["anchor_centroid"], dtype=float)
    rows: list[dict[str, Any]] = []
    for path_s in job["static_files"]:
        path = Path(path_s)
        row = ab.solve_static_file_with_layout(
            path,
            layout=layout,
            solver=solver,
            tag_truth=tag_truth,
            tag_truth_meta=job["tag_truth_meta"],
            metadata_by_id=job["metadata_by_id"],
            metadata=job.get("metadata", {}),
            tag_method=str(job.get("method", "T4")),
            point_estimator=str(job.get("point_estimator", "mean")),
        )
        if row is None:
            continue
        truth = np.asarray([row["truth_x_mm"], row["truth_y_vertical_mm"], row["truth_z_mm"]], dtype=float)
        row["distance_to_array_centroid_mm"] = float(np.linalg.norm(truth - anchor_centroid))
        row["config"] = job.get("config", "")
        row["d_tag_mm"] = float(job["d_tag_mm"])
        row["static_id"] = static_id_from_path(path)
        rows.append(row)
    expected = int(job.get("expected_positions", len(job["static_files"])))
    summary = full.aggregate_static_rows(rows, expected_positions=expected)
    return {
        "job_id": job["job_id"],
        "meta": job.get("meta", {}),
        "d_tag_mm": float(job["d_tag_mm"]),
        "summary": summary,
        "rows": rows if job.get("return_rows", False) else [],
    }


def eval_chunk_worker(chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[_name] = "1"
    return [solve_static_job(job) for job in chunk]


def chunked(items: list[Any], chunk_size: int) -> list[list[Any]]:
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def run_eval_jobs(jobs: list[dict[str, Any]], ctx: dict[str, Any], stage: str, chunk_size: int = 8) -> list[dict[str, Any]]:
    if not jobs:
        return []
    chunks = chunked(jobs, chunk_size)
    done = 0
    results: list[dict[str, Any]] = []
    mp_ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=WORKERS, mp_context=mp_ctx) as pool:
        futures = [pool.submit(eval_chunk_worker, chunk) for chunk in chunks]
        for fut in as_completed(futures):
            part = fut.result()
            results.extend(part)
            done += len(part)
            cpu = sample_cpu(ctx)
            print(json.dumps({"stage": stage, "done": done, "total": len(jobs), "live_cpu_percent": cpu}, sort_keys=True), flush=True)
    by_id = {r["job_id"]: r for r in results}
    return [by_id[j["job_id"]] for j in jobs]


def read_vicon_cm_delays() -> dict[int, float]:
    df = pd.read_csv(require_path(VICON_CM_DELAY, "Vicon common-mode delay table"))
    if "row_type" in df.columns:
        df = df[df["row_type"] == "anchor"]
    if df.empty or "d_anchor_mm" not in df.columns:
        raise RuntimeError(f"cannot parse Vicon cm delays from {VICON_CM_DELAY}")
    return {int(r["anchor_id"]): float(r["d_anchor_mm"]) for _, r in df.iterrows()}


def build_inputs(full) -> dict[str, Any]:
    for path, label in [
        (FULL_V5_SCRIPT, "FULL_V5 helper script"),
        (TRANSFER_SWEEP, "transfer matrix D_tag sweep detail"),
        (TRANSFER_CELLS, "transfer matrix 48-cell table"),
        (VICON_CM_DELAY, "Vicon refit cm delay table"),
        (B_SWEEP, "existing V5 D_tag sweep curve"),
    ]:
        require_path(path, label)
    inputs = full.prepare_inputs()
    truth = inputs["truth_coords"]
    fit_v4 = full.fit_similarity(inputs["coords_v4"], truth, allow_reflection=True, allow_scale=False)
    fit_v5 = full.fit_similarity(inputs["coords_v5"], truth, allow_reflection=True, allow_scale=False)
    inputs["coords_v4_rigid"] = fit_v4.aligned
    inputs["coords_v5_rigid"] = fit_v5.aligned
    inputs["cm_delays"] = read_vicon_cm_delays()
    inputs["static_by_id"] = {static_id_from_path(p): str(p) for p in inputs["static_files"]}
    inputs["tag_truth_np"] = {k: np.asarray(v, dtype=float) for k, v in inputs["tag_truth"].items()}
    inputs["tag_centroid"] = np.vstack([inputs["tag_truth_np"][sid] for sid in sorted(inputs["tag_truth_np"])]).mean(axis=0)
    return inputs


def precompute_median_ranges(full, static_files: list[Path]) -> dict[str, dict[int, float]]:
    medians: dict[str, dict[int, float]] = {}
    for path in static_files:
        sid = static_id_from_path(path)
        frames = full.read_tr_all_frames(path, tags={full.STATIC_TAG}, min_anchors=4)
        by_anchor: dict[int, list[float]] = {aid: [] for aid in range(8)}
        for frame in frames:
            for obs in frame.observations:
                aid = int(obs.anchor_id)
                if 0 <= aid < 8 and float(obs.range_mm) > 0.0:
                    by_anchor[aid].append(float(obs.range_mm))
        medians[sid] = {aid: float(np.nanmedian(vals)) for aid, vals in by_anchor.items() if vals}
    return medians


def load_full_loo_rows() -> dict[tuple[str, str], dict[str, float]]:
    df = pd.read_csv(TRANSFER_CELLS)
    out: dict[tuple[str, str], dict[str, float]] = {}
    for _, r in df[df["tag_delay_mode"] == "D_LOO_CV"].iterrows():
        out[(str(r["layout_source"]), str(r["correction_source"]))] = r.to_dict()
    return out


def build_configs(inputs: dict[str, Any]) -> dict[str, ConfigSpec]:
    loo = load_full_loo_rows()

    def baseline(layout: str, corr: str) -> tuple[float, float, float]:
        row = loo.get((layout, corr))
        if row is None:
            raise RuntimeError(f"missing full LOO row in {TRANSFER_CELLS}: {layout}+{corr}")
        return float(row["median_3d_mm"]), float(row["p95_3d_mm"]), float(row["rmse_3d_mm"])

    v4 = baseline("L_V4", "C_V4")
    v5 = baseline("L_V5", "C_V5")
    vc = baseline("L_Vicon", "C_Vicon_cm")
    return {
        "V4+C_V4": ConfigSpec("V4+C_V4", "L_V4", "C_V4", inputs["coords_v4_rigid"], inputs["delays_v4"], *v4),
        "V5+C_V5": ConfigSpec("V5+C_V5", "L_V5", "C_V5", inputs["coords_v5_rigid"], inputs["delays_v5"], *v5),
        "Vicon+C_Vicon_cm": ConfigSpec("Vicon+C_Vicon_cm", "L_Vicon", "C_Vicon_cm", inputs["truth_coords"], inputs["cm_delays"], *vc),
    }


def assign_positions(inputs: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    rows = []
    ids = sorted(inputs["tag_truth_np"])
    pts = np.vstack([inputs["tag_truth_np"][sid] for sid in ids])
    centroid = pts.mean(axis=0)
    distances = np.linalg.norm(pts - centroid[None, :], axis=1)
    y = pts[:, 1]
    rounded_y = np.unique(np.round(y / 100.0))
    n_tiers = 2 if rounded_y.size <= 2 else 3
    labels = ["LOW", "HIGH"] if n_tiers == 2 else ["LOW", "MID", "HIGH"]
    order = np.argsort(y)
    tier_by_id: dict[str, str] = {}
    for label, inds in zip(labels, np.array_split(order, n_tiers)):
        for idx in inds:
            tier_by_id[ids[int(idx)]] = label
    dist_order = np.argsort(distances)
    edge_by_id: dict[str, str] = {}
    half = len(ids) // 2
    for idx in dist_order[:half]:
        edge_by_id[ids[int(idx)]] = "INNER"
    for idx in dist_order[half:]:
        edge_by_id[ids[int(idx)]] = "OUTER"
    for i, sid in enumerate(ids):
        rows.append(
            {
                "position_id": sid,
                "vicon_x_mm": float(pts[i, 0]),
                "vicon_y_mm": float(pts[i, 1]),
                "vicon_z_mm": float(pts[i, 2]),
                "height_tier": tier_by_id[sid],
                "height_split_mode": f"{n_tiers}_tier_tercile_by_vicon_y",
                "distance_to_centroid_mm": float(distances[i]),
                "edge_center_group": edge_by_id[sid],
            }
        )
    maps = {
        "height": tier_by_id,
        "edge": edge_by_id,
        "distance": {sid: float(distances[i]) for i, sid in enumerate(ids)},
    }
    return rows, maps


def calibrate_dtag(
    train_ids: list[str],
    medians_by_id: dict[str, dict[int, float]],
    tag_truth: dict[str, np.ndarray],
    coords: np.ndarray,
    delays: dict[int, float],
) -> tuple[float, int]:
    residuals: list[float] = []
    for sid in train_ids:
        if sid not in tag_truth:
            continue
        truth = tag_truth[sid]
        for aid, measured in medians_by_id.get(sid, {}).items():
            geom = float(np.linalg.norm(truth - coords[int(aid)]))
            residuals.append(float(measured) - geom - float(delays[int(aid)]))
    return (float(np.nanmedian(residuals)) if residuals else float("nan"), len(residuals))


def make_static_job(
    *,
    job_id: str,
    config: ConfigSpec,
    d_tag_mm: float,
    ids: list[str],
    inputs: dict[str, Any],
    meta: dict[str, Any] | None = None,
    return_rows: bool = False,
) -> dict[str, Any]:
    files = [inputs["static_by_id"][sid] for sid in ids]
    return {
        "job_id": job_id,
        "config": config.config,
        "layout_name": config.config.replace("+", "_"),
        "coords": config.coords.tolist(),
        "delays": config.delays,
        "sigma_by_id": inputs["sigma_by_id"],
        "tag_truth": inputs["tag_truth"],
        "tag_truth_meta": inputs["tag_truth_meta"],
        "metadata_by_id": inputs["metadata_by_id"],
        "static_files": files,
        "anchor_centroid": inputs["truth_coords"].mean(axis=0).tolist(),
        "d_tag_mm": float(d_tag_mm),
        "method": "T4",
        "point_estimator": "mean",
        "expected_positions": len(ids),
        "return_rows": return_rows,
        "metadata": {"mechanism_ablation": True, "config": config.config},
        "meta": meta or {},
    }


def curve_zero_slope(curve: pd.DataFrame, slope_col: str = "signed_vertical_slope_mm_per_m") -> dict[str, Any]:
    df = curve.sort_values("d_tag_mm")
    x = df["d_tag_mm"].to_numpy(dtype=float)
    y = df[slope_col].to_numpy(dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    if x.size == 0:
        return {"d_tag_mm": float("nan"), "value": float("nan"), "zero_slope_found": False, "source": "no_finite_slope"}
    exact = np.where(np.isclose(y, 0.0, atol=1e-9))[0]
    if exact.size:
        i = int(exact[0])
        return {"d_tag_mm": float(x[i]), "value": float(y[i]), "zero_slope_found": True, "source": "exact_grid"}
    crossings = []
    for i in range(len(x) - 1):
        y0, y1 = y[i], y[i + 1]
        if y0 == y1:
            continue
        if (y0 < 0.0 < y1) or (y1 < 0.0 < y0):
            d = float(x[i] - y0 * (x[i + 1] - x[i]) / (y1 - y0))
            crossings.append(d)
    if crossings:
        d = crossings[0]
        return {"d_tag_mm": d, "value": 0.0, "zero_slope_found": True, "source": "linear_crossing"}
    i = int(np.nanargmin(np.abs(y)))
    return {"d_tag_mm": float(x[i]), "value": float(y[i]), "zero_slope_found": False, "source": "nearest_min_abs_slope"}


def interp_metric(curve: pd.DataFrame, d_tag: float, metric: str) -> float:
    df = curve.sort_values("d_tag_mm")
    x = df["d_tag_mm"].to_numpy(dtype=float)
    y = df[metric].to_numpy(dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    if not np.isfinite(d_tag) or x.size == 0:
        return float("nan")
    if d_tag <= x.min() or d_tag >= x.max():
        return float(y[int(np.nanargmin(np.abs(x - d_tag)))])
    return float(np.interp(d_tag, x, y))


def item_e(dirs: dict[str, Path]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item E")
    df = pd.read_csv(TRANSFER_SWEEP)
    needed = [
        ("L_V4", "C_V4"),
        ("L_V5", "C_V5"),
        ("L_V4", "C_none"),
        ("L_V4", "C_V5"),
        ("L_Vicon", "C_Vicon_cm"),
        ("L_V5", "C_none"),
    ]
    rows = []
    for layout, corr in needed:
        sub = df[(df["layout_source"] == layout) & (df["correction_source"] == corr)].copy()
        if sub.empty:
            raise RuntimeError(f"missing D_tag curve in {TRANSFER_SWEEP}: {layout}+{corr}")
        for _, r in sub.sort_values("d_tag_mm").iterrows():
            rows.append(
                {
                    "config": f"{layout}+{corr}",
                    "layout_source": layout,
                    "correction_source": corr,
                    "d_tag_mm": float(r["d_tag_mm"]),
                    "median_3d_mm": float(r["median_3d_mm"]),
                    "rmse_3d_mm": float(r["rmse_3d_mm"]),
                    "p95_3d_mm": float(r["p95_3d_mm"]),
                    "signed_vertical_slope_mm_per_m": float(r["signed_vertical_slope_mm_per_m"]),
                }
            )
    curves = pd.DataFrame(rows)
    critical = []
    spread = []
    for config, g in curves.groupby("config"):
        points: dict[str, float] = {}
        for criterion, metric in [("min_median", "median_3d_mm"), ("min_rmse", "rmse_3d_mm"), ("min_p95", "p95_3d_mm")]:
            idx = g[metric].astype(float).idxmin()
            r = g.loc[idx]
            points[criterion] = float(r["d_tag_mm"])
            critical.append({"config": config, "criterion": criterion, "d_tag_mm": float(r["d_tag_mm"]), "value_at_point": float(r[metric]), "zero_slope_found": ""})
        z = curve_zero_slope(g)
        points["zero_slope"] = z["d_tag_mm"]
        critical.append({"config": config, "criterion": "zero_slope", "d_tag_mm": z["d_tag_mm"], "value_at_point": z["value"], "zero_slope_found": z["zero_slope_found"], "source": z["source"]})
        vals = [v for v in points.values() if np.isfinite(v)]
        spread.append(
            {
                "config": config,
                "d_tag_min_median": points["min_median"],
                "d_tag_min_rmse": points["min_rmse"],
                "d_tag_min_p95": points["min_p95"],
                "d_tag_zero_slope": points["zero_slope"],
                "zero_slope_found": z["zero_slope_found"],
                "spread_mm": float(max(vals) - min(vals)) if vals else float("nan"),
            }
        )
    write_csv(dirs["E"] / "dtag_curves_extracted.csv", rows)
    write_csv(dirs["E"] / "dtag_curves_critical_points.csv", critical)
    write_csv(dirs["E"] / "dtag_curves_spread.csv", spread)
    sample_cpu(ctx)
    report = finish_phase(ctx)
    print("# Item E - D_tag curves\n", flush=True)
    print(pd.DataFrame(spread).to_string(index=False), flush=True)
    return report, rows, critical


def item_f(dirs: dict[str, Path], curves_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item F")
    curves = pd.DataFrame(curves_rows)
    cells = pd.read_csv(TRANSFER_CELLS)
    rows = []
    spread_rows = []
    for config, g in curves.groupby("config"):
        layout, corr = config.split("+", 1)
        entries: list[tuple[str, float]] = []
        for criterion, metric in [("min_median", "median_3d_mm"), ("min_rmse", "rmse_3d_mm"), ("min_p95", "p95_3d_mm")]:
            r = g.loc[g[metric].astype(float).idxmin()]
            entries.append((criterion, float(r["d_tag_mm"])))
        z = curve_zero_slope(g)
        entries.append(("zero_slope", float(z["d_tag_mm"])))
        loo = cells[(cells["layout_source"] == layout) & (cells["correction_source"] == corr) & (cells["tag_delay_mode"] == "D_LOO_CV")]
        entries.append(("loo_cv", LOO_DTAG_MM))
        dvals = []
        out_by_crit = {}
        for criterion, dtag in entries:
            dvals.append(dtag)
            if criterion == "loo_cv" and not loo.empty:
                row_metrics = loo.iloc[0].to_dict()
                median_v = float(row_metrics["median_3d_mm"])
                rmse_v = float(row_metrics["rmse_3d_mm"])
                p95_v = float(row_metrics["p95_3d_mm"])
                slope_v = float(row_metrics["signed_vertical_slope_mm_per_m"])
            else:
                median_v = interp_metric(g, dtag, "median_3d_mm")
                rmse_v = interp_metric(g, dtag, "rmse_3d_mm")
                p95_v = interp_metric(g, dtag, "p95_3d_mm")
                slope_v = interp_metric(g, dtag, "signed_vertical_slope_mm_per_m")
            out_by_crit[criterion] = dtag
            rows.append(
                {
                    "config": config,
                    "criterion": criterion,
                    "optimal_d_tag_mm": dtag,
                    "median_3d_at_opt": median_v,
                    "rmse_3d_at_opt": rmse_v,
                    "p95_3d_at_opt": p95_v,
                    "slope_at_opt": slope_v,
                }
            )
        finite_d = [v for v in dvals if np.isfinite(v)]
        spread_rows.append(
            {
                "config": config,
                "d_tag_min_median": out_by_crit.get("min_median", float("nan")),
                "d_tag_min_rmse": out_by_crit.get("min_rmse", float("nan")),
                "d_tag_min_p95": out_by_crit.get("min_p95", float("nan")),
                "d_tag_zero_slope": out_by_crit.get("zero_slope", float("nan")),
                "d_tag_loo_cv": LOO_DTAG_MM,
                "spread_mm": float(max(finite_d) - min(finite_d)) if finite_d else float("nan"),
            }
        )
    write_csv(dirs["F"] / "multi_criterion_dtag.csv", rows)
    write_csv(dirs["F"] / "multi_criterion_spread.csv", spread_rows)
    sample_cpu(ctx)
    report = finish_phase(ctx)
    print("# Item F - Multi-criterion D_tag\n", flush=True)
    print(pd.DataFrame(spread_rows).to_string(index=False), flush=True)
    return report, rows, spread_rows


def item_a(
    dirs: dict[str, Path],
    inputs: dict[str, Any],
    configs: dict[str, ConfigSpec],
    medians_by_id: dict[str, dict[int, float]],
    assignments: list[dict[str, Any]],
    maps: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item A")
    write_csv(dirs["A"] / "hard_cv_position_tier_assignments.csv", assignments)
    ids = sorted(inputs["tag_truth_np"])
    jobs = []
    job_meta = []
    for config in configs.values():
        tiers = sorted(set(maps["height"].values()), key=lambda x: ["LOW", "MID", "HIGH"].index(x) if x in ["LOW", "MID", "HIGH"] else x)
        for tier in tiers:
            eval_ids = [sid for sid in ids if maps["height"][sid] == tier]
            train_ids = [sid for sid in ids if maps["height"][sid] != tier]
            dtag, terms = calibrate_dtag(train_ids, medians_by_id, inputs["tag_truth_np"], config.coords, config.delays)
            meta = {"split_type": "height_tier", "held_out_tier": tier, "n_train": len(train_ids), "n_eval": len(eval_ids), "d_tag_fit_terms": terms}
            job_id = f"A_height_{config.config}_{tier}".replace("+", "_")
            jobs.append(make_static_job(job_id=job_id, config=config, d_tag_mm=dtag, ids=eval_ids, inputs=inputs, meta=meta))
            job_meta.append((job_id, meta, config))
        for group in ["OUTER", "INNER"]:
            eval_ids = [sid for sid in ids if maps["edge"][sid] == group]
            train_ids = [sid for sid in ids if maps["edge"][sid] != group]
            dtag, terms = calibrate_dtag(train_ids, medians_by_id, inputs["tag_truth_np"], config.coords, config.delays)
            meta = {"split_type": "edge_center", "held_out_group": group, "n_train": len(train_ids), "n_eval": len(eval_ids), "d_tag_fit_terms": terms}
            job_id = f"A_edge_{config.config}_{group}".replace("+", "_")
            jobs.append(make_static_job(job_id=job_id, config=config, d_tag_mm=dtag, ids=eval_ids, inputs=inputs, meta=meta))
            job_meta.append((job_id, meta, config))
    results = run_eval_jobs(jobs, ctx, "item_A_hard_cv", chunk_size=3)
    by_id = {r["job_id"]: r for r in results}
    height_rows = []
    edge_rows = []
    for job_id, meta, config in job_meta:
        r = by_id[job_id]
        s = r["summary"]
        row = {
            "config": config.config,
            "d_tag_calibrated_mm": r["d_tag_mm"],
            "n_train": meta["n_train"],
            "n_eval": meta["n_eval"],
            "median_3d_mm": s["median_3d_mm"],
            "p95_3d_mm": s["p95_3d_mm"],
            "rmse_3d_mm": s["rmse_3d_mm"],
            "median_vert_mm": s["median_vert_mm"],
            "signed_vert_slope": s["signed_vertical_slope_mm_per_m"],
            "signed_vertical_slope_r2": s["signed_vertical_slope_r2"],
            "fail_rate": s["fail_rate"],
        }
        if meta["split_type"] == "height_tier":
            height_rows.append({"held_out_tier": meta["held_out_tier"], **row})
        else:
            edge_rows.append({"held_out": meta["held_out_group"], **row})
    summary = []
    for config in configs.values():
        h = [r for r in height_rows if r["config"] == config.config]
        e = [r for r in edge_rows if r["config"] == config.config]
        worst_h = max(h, key=lambda r: float(r["median_3d_mm"]))
        worst_e = max(e, key=lambda r: float(r["median_3d_mm"]))
        summary.append(
            {
                "config": config.config,
                "full_loo_median_3d_mm": config.full_loo_median_mm,
                "worst_tier": worst_h["held_out_tier"],
                "worst_tier_median_3d_mm": worst_h["median_3d_mm"],
                "height_degradation_mm": float(worst_h["median_3d_mm"]) - config.full_loo_median_mm,
                "worst_edge_center": worst_e["held_out"],
                "worst_edge_center_median_3d_mm": worst_e["median_3d_mm"],
                "edge_center_degradation_mm": float(worst_e["median_3d_mm"]) - config.full_loo_median_mm,
            }
        )
    write_csv(dirs["A"] / "hard_cv_height_tier_results.csv", height_rows)
    write_csv(dirs["A"] / "hard_cv_edge_center_results.csv", edge_rows)
    write_csv(dirs["A"] / "hard_cv_summary.csv", summary)
    report = finish_phase(ctx)
    print("# Item A - Hard CV\n", flush=True)
    print(pd.DataFrame(summary).to_string(index=False), flush=True)
    return report, height_rows, edge_rows, summary


def item_b(
    dirs: dict[str, Path],
    inputs: dict[str, Any],
    configs: dict[str, ConfigSpec],
    maps: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item B")
    ids = sorted(inputs["tag_truth_np"])
    use = [configs["V4+C_V4"], configs["V5+C_V5"]]
    jobs = [
        make_static_job(job_id=f"B_{c.config}".replace("+", "_"), config=c, d_tag_mm=LOO_DTAG_MM, ids=ids, inputs=inputs, return_rows=True)
        for c in use
    ]
    results = run_eval_jobs(jobs, ctx, "item_B_residual_field", chunk_size=1)
    per_pos = []
    for result in results:
        config = result["meta"].get("config", "") or next((j["config"] for j in jobs if j["job_id"] == result["job_id"]), "")
        for row in result["rows"]:
            sid = row["ID"]
            ex = float(row["err_x_mm"])
            ey = float(row["err_y_vertical_mm"])
            ez = float(row["err_z_mm"])
            norm = math.sqrt(ex * ex + ey * ey + ez * ez)
            per_pos.append(
                {
                    "position_id": sid,
                    "config": config,
                    "truth_x_mm": row["truth_x_mm"],
                    "truth_y_mm": row["truth_y_vertical_mm"],
                    "truth_z_mm": row["truth_z_mm"],
                    "solved_x_mm": row["solved_x_mm"],
                    "solved_y_mm": row["solved_y_vertical_mm"],
                    "solved_z_mm": row["solved_z_mm"],
                    "error_x_signed_mm": ex,
                    "error_y_signed_mm": ey,
                    "error_z_signed_mm": ez,
                    "error_3d_mm": row["err_3d_mm"],
                    "error_horiz_mm": row["err_horizontal_xz_mm"],
                    "error_vert_signed_mm": ey,
                    "error_unit_x": ex / norm if norm > 1e-9 else float("nan"),
                    "error_unit_y": ey / norm if norm > 1e-9 else float("nan"),
                    "error_unit_z": ez / norm if norm > 1e-9 else float("nan"),
                    "height_tier": maps["height"].get(sid, ""),
                    "edge_center_group": maps["edge"].get(sid, ""),
                    "distance_to_centroid_mm": maps["distance"].get(sid, float("nan")),
                }
            )
    reg_rows = []
    summary_rows = []
    df = pd.DataFrame(per_pos)
    for config, g in df.groupby("config"):
        rv = regress_mm_per_m(g["truth_y_mm"], g["error_vert_signed_mm"])
        reg_rows.append({"config": config, "metric": "vert_vs_height", "slope": rv["slope"], "intercept": rv["intercept"], "r2": rv["r2"], "p_value": rv["p_value"]})
        rh = regress_mm_per_m(g["distance_to_centroid_mm"], g["error_horiz_mm"])
        reg_rows.append({"config": config, "metric": "horiz_vs_centroid", "slope": rh["slope"], "intercept": rh["intercept"], "r2": rh["r2"], "p_value": rh["p_value"]})
        err = g[["error_x_signed_mm", "error_y_signed_mm", "error_z_signed_mm"]].to_numpy(dtype=float)
        units = g[["error_unit_x", "error_unit_y", "error_unit_z"]].to_numpy(dtype=float)
        mean_vec = np.nanmean(err, axis=0)
        flat = err.reshape(-1)
        summary_rows.append(
            {
                "config": config,
                "mean_signed_error_x": float(mean_vec[0]),
                "mean_signed_error_y": float(mean_vec[1]),
                "mean_signed_error_z": float(mean_vec[2]),
                "mean_signed_error_magnitude": float(np.linalg.norm(mean_vec)),
                "structured_bias_index": float(np.nanstd(flat) / np.nanmean(np.abs(flat))),
                "mean_error_direction_resultant": float(np.linalg.norm(np.nanmean(units, axis=0))),
                "median_3d": percentile(g["error_3d_mm"], 50),
                "rmse_3d": rmse(g["error_3d_mm"]),
            }
        )
    write_csv(dirs["B"] / "residual_field_per_position.csv", per_pos)
    write_csv(dirs["B"] / "residual_field_regression.csv", reg_rows)
    write_csv(dirs["B"] / "residual_field_summary.csv", summary_rows)
    report = finish_phase(ctx)
    print("# Item B - Residual field\n", flush=True)
    print(pd.DataFrame(summary_rows).to_string(index=False), flush=True)
    return report, per_pos, reg_rows, summary_rows


def item_d(
    dirs: dict[str, Path],
    inputs: dict[str, Any],
    configs: dict[str, ConfigSpec],
    maps: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item D")
    ids = sorted(inputs["tag_truth_np"])
    tiers = sorted(set(maps["height"].values()), key=lambda x: ["LOW", "MID", "HIGH"].index(x) if x in ["LOW", "MID", "HIGH"] else x)
    grid = [float(x) for x in np.arange(0.0, 120.0 + 0.1, 2.0)]
    jobs = []
    for config in configs.values():
        for tier in tiers:
            tier_ids = [sid for sid in ids if maps["height"][sid] == tier]
            for dtag in grid:
                job_id = f"D_{config.config}_{tier}_{dtag:.1f}".replace("+", "_")
                jobs.append(make_static_job(job_id=job_id, config=config, d_tag_mm=dtag, ids=tier_ids, inputs=inputs, meta={"height_tier": tier}))
    results = run_eval_jobs(jobs, ctx, "item_D_per_height_dtag", chunk_size=6)
    sweep_rows = []
    for r in results:
        tier = r["meta"]["height_tier"]
        s = r["summary"]
        config = next(j["config"] for j in jobs if j["job_id"] == r["job_id"])
        sweep_rows.append(
            {
                "config": config,
                "height_tier": tier,
                "d_tag_mm": r["d_tag_mm"],
                "n_positions": s["n_positions"],
                "median_3d_mm": s["median_3d_mm"],
                "rmse_3d_mm": s["rmse_3d_mm"],
                "p95_3d_mm": s["p95_3d_mm"],
                "signed_vertical_slope": s["signed_vertical_slope_mm_per_m"],
            }
        )
    opt_rows = []
    df = pd.DataFrame(sweep_rows)
    for (config, tier), g in df.groupby(["config", "height_tier"]):
        r_med = g.loc[g["median_3d_mm"].astype(float).idxmin()]
        r_rmse = g.loc[g["rmse_3d_mm"].astype(float).idxmin()]
        z = curve_zero_slope(g.rename(columns={"signed_vertical_slope": "signed_vertical_slope_mm_per_m"}))
        opt_rows.append(
            {
                "config": config,
                "height_tier": tier,
                "n_positions": int(r_med["n_positions"]),
                "d_tag_min_median_mm": float(r_med["d_tag_mm"]),
                "median_at_min": float(r_med["median_3d_mm"]),
                "d_tag_min_rmse_mm": float(r_rmse["d_tag_mm"]),
                "rmse_at_min": float(r_rmse["rmse_3d_mm"]),
                "d_tag_zero_slope_mm": z["d_tag_mm"],
                "zero_slope_found": z["zero_slope_found"],
                "slope_value_at_zero_choice": z["value"],
            }
        )
    write_csv(dirs["D"] / "per_height_dtag_sweep.csv", sweep_rows)
    write_csv(dirs["D"] / "per_height_dtag_optima.csv", opt_rows)
    report = finish_phase(ctx)
    print("# Item D - Per-height D_tag\n", flush=True)
    print(pd.DataFrame(opt_rows).to_string(index=False), flush=True)
    return report, sweep_rows, opt_rows


def item_c(
    dirs: dict[str, Path],
    inputs: dict[str, Any],
    configs: dict[str, ConfigSpec],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item C")
    v5 = configs["V5+C_V5"]
    centroid = v5.coords.mean(axis=0)
    scales = [float(x) for x in np.round(np.arange(0.93, 1.05 + 0.0001, 0.005), 3)]
    dgrid = [float(x) for x in np.arange(0.0, 120.0 + 0.1, 2.0)]
    ids = sorted(inputs["tag_truth_np"])
    jobs = []
    for s in scales:
        scaled = centroid[None, :] + s * (v5.coords - centroid[None, :])
        for dtag in dgrid:
            job_id = f"C_s{s:.3f}_d{dtag:.1f}"
            jobs.append(make_static_job(job_id=job_id, config=v5, d_tag_mm=dtag, ids=ids, inputs=inputs, meta={"s": s}, return_rows=False) | {"coords": scaled.tolist()})
    results = run_eval_jobs(jobs, ctx, "item_C_cancellation_valley", chunk_size=8)
    rows = []
    for r in results:
        s = float(r["meta"]["s"])
        sm = r["summary"]
        rows.append(
            {
                "s": s,
                "d_tag_mm": r["d_tag_mm"],
                "median_3d_mm": sm["median_3d_mm"],
                "rmse_3d_mm": sm["rmse_3d_mm"],
                "p95_3d_mm": sm["p95_3d_mm"],
                "signed_vertical_slope": sm["signed_vertical_slope_mm_per_m"],
                "n_positions": sm["n_positions"],
                "n_frames": sm["n_frames"],
            }
        )
    df = pd.DataFrame(rows)
    gmin = df.loc[df["median_3d_mm"].astype(float).idxmin()].to_dict()
    transfer = pd.read_csv(TRANSFER_CELLS)
    v5_loo = transfer[(transfer["layout_source"] == "L_V5") & (transfer["correction_source"] == "C_V5") & (transfer["tag_delay_mode"] == "D_LOO_CV")].iloc[0]
    scale_cmp = pd.read_csv(ANALYSIS / "FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv")
    v4_scale = float(scale_cmp[scale_cmp["layout"] == "v4-io"].iloc[0]["sim3_scale"])
    v5_scale = float(scale_cmp[scale_cmp["layout"] == "v5-commonmode"].iloc[0]["sim3_scale"])
    v4_equiv_s = v4_scale / v5_scale
    nearest_s = min(scales, key=lambda x: abs(x - v4_equiv_s))
    v4_near = df[(df["s"] == nearest_s) & (df["d_tag_mm"] == 0.0)].iloc[0]
    markers = [
        {"marker_name": "V5_LOO", "s": 1.0, "d_tag_mm": LOO_DTAG_MM, "median_3d_mm": float(v5_loo["median_3d_mm"]), "source": "exact_transfer_matrix"},
        {"marker_name": "V4_equiv", "s": v4_equiv_s, "nearest_grid_s": nearest_s, "d_tag_mm": 0.0, "median_3d_mm": float(v4_near["median_3d_mm"]), "source": "nearest_grid"},
        {"marker_name": "global_min", "s": float(gmin["s"]), "d_tag_mm": float(gmin["d_tag_mm"]), "median_3d_mm": float(gmin["median_3d_mm"]), "source": "grid"},
    ]
    write_csv(dirs["C"] / "cancellation_valley_grid.csv", rows)
    write_csv(dirs["C"] / "cancellation_valley_markers.csv", markers)
    report = finish_phase(ctx)
    print("# Item C - Cancellation valley\n", flush=True)
    print(pd.DataFrame(markers).to_string(index=False), flush=True)
    return report, rows, markers


def runtime_summary_text(runtime_rows: list[dict[str, Any]], total_wall: float) -> str:
    mean_cpu = float(np.nanmean([r["mean_cpu_percent"] for r in runtime_rows]))
    max_cpu = float(np.nanmax([r["max_cpu_percent"] for r in runtime_rows]))
    by_item = {r["item"]: r["elapsed_s"] for r in runtime_rows}
    lines = [
        "=== V5 MECHANISM ABLATION - RUNTIME SUMMARY ===\n",
        "Machine: i7-8700K 6C/12T 32GB\n",
        "Workers: 6 (process pool), GPU idle\n\n",
        f"Item A (hard CV):              {by_item.get('Item A', float('nan')):.1f} s\n",
        f"Item B (residual field):       {by_item.get('Item B', float('nan')):.1f} s\n",
        f"Item C (cancellation valley):  {by_item.get('Item C', float('nan')):.1f} s\n",
        f"Item D (per-height D_tag):     {by_item.get('Item D', float('nan')):.1f} s\n",
        f"Item E (D_tag curves):         {by_item.get('Item E', float('nan')):.1f} s\n",
        f"Item F (multi-criterion):      {by_item.get('Item F', float('nan')):.1f} s\n",
        f"Total wall time:               {total_wall:.1f} s\n\n",
        f"Mean CPU%: {mean_cpu:.1f}%\n",
        f"Max CPU%:  {max_cpu:.1f}%\n",
    ]
    return "".join(lines)


def build_final_report(
    dirs: dict[str, Path],
    runtime_rows: list[dict[str, Any]],
    a_summary: list[dict[str, Any]],
    b_summary: list[dict[str, Any]],
    c_markers: list[dict[str, Any]],
    d_optima: list[dict[str, Any]],
    e_spread: list[dict[str, Any]],
    f_spread: list[dict[str, Any]],
) -> str:
    lines = ["# V5 Mechanism Ablation Summary\n\n"]
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")
    lines.append("## Synthesis\n\n")
    a_df = pd.DataFrame(a_summary)
    if not a_df.empty:
        v4_deg = float(a_df[a_df["config"] == "V4+C_V4"].iloc[0]["height_degradation_mm"])
        v5_deg = float(a_df[a_df["config"] == "V5+C_V5"].iloc[0]["height_degradation_mm"])
        lines.append(f"Hard height-tier CV degradation is {v4_deg:.1f} mm for V4+C_V4 and {v5_deg:.1f} mm for V5+C_V5. ")
        if v4_deg > v5_deg + 10.0:
            lines.append("That supports dataset-specific cancellation in the V4+LOO advantage. ")
        elif v5_deg > v4_deg + 10.0:
            lines.append("That means V5 degrades more under this split, so the cancellation claim is not supported by hard CV alone. ")
        else:
            lines.append("The hard-CV degradation is similar, so the evidence is mixed rather than decisive. ")
    lines.append("Use the tables below to separate validation robustness, residual-field structure, scale-delay valley shape, and D_tag criterion ambiguity.\n\n")

    lines.append("## Item A - Hard CV\n\n")
    append_md_table(lines, a_summary, ["config", "full_loo_median_3d_mm", "worst_tier", "worst_tier_median_3d_mm", "height_degradation_mm", "worst_edge_center", "edge_center_degradation_mm"])

    lines.append("## Item B - Residual Field\n\n")
    append_md_table(lines, b_summary, ["config", "mean_signed_error_magnitude", "structured_bias_index", "mean_error_direction_resultant", "median_3d", "rmse_3d"])

    lines.append("## Item C - Cancellation Valley\n\n")
    append_md_table(lines, c_markers, ["marker_name", "s", "nearest_grid_s", "d_tag_mm", "median_3d_mm", "source"])

    lines.append("## Item D - Per-height D_tag Stability\n\n")
    d_rows = []
    for config, g in pd.DataFrame(d_optima).groupby("config"):
        vals = g["d_tag_min_median_mm"].to_numpy(dtype=float)
        d_rows.append({"config": config, "min_dtag_min_median": float(np.nanmin(vals)), "max_dtag_min_median": float(np.nanmax(vals)), "tier_spread_mm": float(np.nanmax(vals) - np.nanmin(vals))})
    append_md_table(lines, d_rows, ["config", "min_dtag_min_median", "max_dtag_min_median", "tier_spread_mm"])

    lines.append("## Item E - D_tag Curve Critical Points\n\n")
    append_md_table(lines, e_spread, ["config", "d_tag_min_median", "d_tag_min_rmse", "d_tag_min_p95", "d_tag_zero_slope", "spread_mm"])

    lines.append("## Item F - Multi-criterion D_tag\n\n")
    append_md_table(lines, f_spread, ["config", "d_tag_min_median", "d_tag_min_rmse", "d_tag_min_p95", "d_tag_zero_slope", "d_tag_loo_cv", "spread_mm"])

    lines.append("## Runtime\n\n")
    append_md_table(lines, runtime_rows, ["item", "elapsed_s", "mean_cpu_percent", "max_cpu_percent", "physical_cores", "logical_cores", "workers"])
    report = "".join(lines)
    (dirs["reports"] / "MECHANISM_ABLATION_SUMMARY.md").write_text(report, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run V5 mechanism ablations into FULL_V5_mechanism_ablations.")
    parser.parse_args()
    global_start = time.perf_counter()
    dirs = make_dirs()
    full = load_full_module()
    print(json.dumps({"stage": "start", "analysis": str(ANALYSIS), "output": str(OUT_ROOT), "workers": WORKERS, "gpu": "idle_not_used"}, sort_keys=True), flush=True)
    inputs = build_inputs(full)
    configs = build_configs(inputs)
    assignments, maps = assign_positions(inputs)
    medians_by_id = precompute_median_ranges(full, inputs["static_files"])
    runtime_rows: list[dict[str, Any]] = []

    e_report, e_curves, _e_critical = item_e(dirs)
    runtime_rows.append(e_report)
    e_spread = pd.read_csv(dirs["E"] / "dtag_curves_spread.csv").to_dict("records")

    f_report, _f_rows, f_spread = item_f(dirs, e_curves)
    runtime_rows.append(f_report)

    a_report, _a_height, _a_edge, a_summary = item_a(dirs, inputs, configs, medians_by_id, assignments, maps)
    runtime_rows.append(a_report)

    b_report, _b_per, _b_reg, b_summary = item_b(dirs, inputs, configs, maps)
    runtime_rows.append(b_report)

    d_report, _d_sweep, d_optima = item_d(dirs, inputs, configs, maps)
    runtime_rows.append(d_report)

    c_report, _c_grid, c_markers = item_c(dirs, inputs, configs)
    runtime_rows.append(c_report)

    total_wall = time.perf_counter() - global_start
    write_csv(dirs["reports"] / "runtime_summary.csv", runtime_rows)
    summary = runtime_summary_text(runtime_rows, total_wall)
    (dirs["reports"] / "RUNTIME_SUMMARY.txt").write_text(summary, encoding="utf-8")
    report = build_final_report(dirs, runtime_rows, a_summary, b_summary, c_markers, d_optima, e_spread, f_spread)
    print(summary, flush=True)
    print(report, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
