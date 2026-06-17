#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from multiprocessing import get_context
from pathlib import Path
from typing import Any

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import numpy as np
import pandas as pd
import psutil

THIS = Path(__file__).resolve()
COMPARISON_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
OFFICIAL_ROOT = THIS.parents[4]
FULL_ROOT = EXTRA_ROOT / "FULL"
ABLATION_SCRIPT = COMPARISON_ROOT / "scripts/run_static_layout_ablation.py"
LAYOUT_BASE = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check"
V5_LAYOUT = LAYOUT_BASE / "v5-commonmode/layout.json"
SIGMA_PATH = LAYOUT_BASE / "tables/anchor_sigma.json"
STATIC_TABLE = LAYOUT_BASE / "tables/static_all_captures.csv"
CAPTURES_ROOT = OFFICIAL_ROOT / "captures/erlangen_20260528_optitrack"
OPTI_ROOT = OFFICIAL_ROOT / "opti_captures/full"
LOO_SUMMARY = COMPARISON_ROOT / "tables/v5_loo_tag_delay_summary.csv"
ANCHORS = list("ABCDEFGH")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def deployable_loo_dtag() -> float:
    df = pd.read_csv(LOO_SUMMARY)
    row = df[(df["variant"] == "deployable_v5_rigid_selfcal") & (df["case"] == "loo_tag_delay")]
    if row.empty:
        raise RuntimeError(f"missing deployable LOO row in {LOO_SUMMARY}")
    return float(row.iloc[0]["d_tag_median_mm"])


def regression_slope_mm_per_m(x_mm: np.ndarray, y_mm: np.ndarray) -> tuple[float, float]:
    mask = np.isfinite(x_mm) & np.isfinite(y_mm)
    xx = x_mm[mask]
    yy = y_mm[mask]
    if xx.size < 3 or float(np.nanstd(xx)) <= 1e-12:
        return float("nan"), float("nan")
    slope, intercept = np.polyfit(xx, yy, 1)
    pred = slope * xx + intercept
    ss_res = float(np.sum((yy - pred) ** 2))
    ss_tot = float(np.sum((yy - np.mean(yy)) ** 2))
    return float(slope * 1000.0), float(1.0 - ss_res / ss_tot) if ss_tot > 0.0 else float("nan")


def summarize_position_rows(rows: list[dict[str, Any]], dtag: float, wall_s: float) -> dict[str, Any]:
    df = pd.DataFrame(rows)
    err = df["err_3d_mm"].to_numpy(dtype=float)
    vertical = df["err_vertical_y_mm"].to_numpy(dtype=float)
    slope, r2 = regression_slope_mm_per_m(
        df["truth_y_vertical_mm"].to_numpy(dtype=float),
        df["err_y_vertical_mm"].to_numpy(dtype=float),
    )
    return {
        "d_tag_mm": float(dtag),
        "n_positions": int(len(df)),
        "frames_input_total": int(df["frames_input"].sum()),
        "frames_solved_total": int(df["frames_solved"].sum()),
        "median_3d_mm": float(np.nanmedian(err)),
        "p95_3d_mm": float(np.nanpercentile(err, 95)),
        "rmse_3d_mm": float(math.sqrt(np.nanmean(err * err))),
        "median_abs_vertical_mm": float(np.nanmedian(vertical)),
        "signed_vertical_slope_mm_per_m": slope,
        "signed_vertical_r2": r2,
        "wall_s": float(wall_s),
    }


def worker(job: dict[str, Any]) -> dict[str, Any]:
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    t0 = time.perf_counter()
    ablation = load_module(Path(job["ablation_script"]), f"dtag_sweep_ablation_helpers_{os.getpid()}")
    dtag = float(job["d_tag_mm"])
    layout = ablation.build_layout(
        name=f"v5_commonmode_deployable_fixed_dtag_{dtag:.3f}",
        labels=ANCHORS,
        coords_opti_frame=np.asarray(job["coords_rigid"], dtype=float),
        delays={int(k): float(v) for k, v in job["delays"].items()},
        tag_delay_mm=dtag,
        sigma_by_id={int(k): float(v) for k, v in job["sigma_by_id"].items()},
        metadata={"variant": "deployable_v5_rigid_selfcal", "case": "fixed_scalar_sweep", "d_tag_mm": dtag},
    )
    solver = ablation.TagPositionSolver(layout, ablation.SolverConfig(method="T4"))
    tag_truth = {k: np.asarray(v, dtype=float) for k, v in job["tag_truth"].items()}
    rows: list[dict[str, Any]] = []
    for path_s in job["static_files"]:
        path = Path(path_s)
        row = ablation.solve_static_file_with_layout(
            path,
            layout=layout,
            solver=solver,
            tag_truth=tag_truth,
            tag_truth_meta=job["tag_truth_meta"],
            metadata_by_id=job["metadata_by_id"],
            metadata={
                "variant": "deployable_v5_rigid_selfcal",
                "case": "fixed_scalar_sweep",
                "d_tag_mm": dtag,
                "source_v5_layout": job["v5_layout"],
            },
            tag_method="T4",
            point_estimator="mean",
        )
        if row is not None:
            rows.append(row)
    wall_s = time.perf_counter() - t0
    return {"d_tag_mm": dtag, "summary": summarize_position_rows(rows, dtag, wall_s)}


def build_grid(step_mm: float, include: list[float]) -> list[float]:
    if step_mm <= 0.0 or not np.isfinite(step_mm):
        raise ValueError(f"step must be positive, got {step_mm!r}")
    vals = [float(v) for v in np.arange(0.0, 120.0 + 0.5 * step_mm, step_mm)]
    vals.extend(float(v) for v in include if np.isfinite(v) and 0.0 <= float(v) <= 120.0)
    return sorted({round(v, 6) for v in vals})


def row_at_or_near(df: pd.DataFrame, dtag: float) -> dict[str, Any]:
    idx = int(np.nanargmin(np.abs(df["d_tag_mm"].to_numpy(dtype=float) - float(dtag))))
    row = df.iloc[idx].to_dict()
    row["requested_d_tag_mm"] = float(dtag)
    row["abs_d_tag_delta_mm"] = abs(float(row["d_tag_mm"]) - float(dtag))
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep fixed scalar D_tag on the deployable V5 self-cal layout.")
    parser.add_argument("--out-dir", type=Path, default=COMPARISON_ROOT / "tables")
    parser.add_argument("--step-mm", type=float, default=2.0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    tables = args.out_dir.resolve()
    curve_path = tables / "B_dtag_sweep_curve.csv"
    summary_path = tables / "B_dtag_sweep_summary.csv"
    runtime_path = tables / "B_dtag_sweep_runtime.csv"
    if not args.replace:
        for path in (curve_path, summary_path, runtime_path):
            if path.exists():
                raise SystemExit(f"refusing to overwrite existing output without --replace: {path}")
    tables.mkdir(parents=True, exist_ok=True)

    loo_dtag = deployable_loo_dtag()
    grid = build_grid(args.step_mm, [loo_dtag])
    ablation = load_module(ABLATION_SCRIPT, "dtag_sweep_ablation_helpers_main")
    labels, coords, v5_delays, _layout_tag_delay = ablation.load_layout_json_raw(V5_LAYOUT)
    by_label = {label: coords[i] for i, label in enumerate(labels)}
    src = np.vstack([by_label[a] for a in ANCHORS])
    anchor_truth, tag_truth_np, tag_truth_meta, _corr = ablation.load_corrected_static_truth(
        OPTI_ROOT,
        ANCHORS,
        ablation.PRIMARY_IDS,
    )
    truth_coords = np.vstack([anchor_truth[a] for a in ANCHORS])
    rigid = ablation.fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=False)
    coords_rigid = ablation.apply_fit(src, rigid)
    sigma_by_id = ablation.load_anchor_sigma(SIGMA_PATH)
    metadata_by_id = ablation.load_static_metadata(STATIC_TABLE)
    static_files = sorted(CAPTURES_ROOT.glob("static_ID*/tag_capture*/tr_all.csv"))
    tag_truth = {k: v.tolist() for k, v in tag_truth_np.items()}

    physical = psutil.cpu_count(logical=False) or 6
    logical = psutil.cpu_count(logical=True) or physical
    workers = max(1, min(int(args.workers), len(grid), physical))
    base_job = {
        "ablation_script": str(ABLATION_SCRIPT),
        "coords_rigid": coords_rigid.tolist(),
        "delays": {int(k): float(v) for k, v in v5_delays.items()},
        "sigma_by_id": {int(k): float(v) for k, v in sigma_by_id.items()},
        "tag_truth": tag_truth,
        "tag_truth_meta": tag_truth_meta,
        "metadata_by_id": metadata_by_id,
        "static_files": [str(p) for p in static_files],
        "v5_layout": str(V5_LAYOUT),
    }
    jobs = [{**base_job, "d_tag_mm": dtag} for dtag in grid]
    print(
        json.dumps(
            {
                "stage": "b_sweep_start",
                "grid_values": len(grid),
                "step_mm": args.step_mm,
                "loo_dtag_mm": loo_dtag,
                "physical_cores": physical,
                "logical_cores": logical,
                "workers": workers,
                "blas_threads_per_worker": {k: os.environ.get(k) for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")},
            },
            sort_keys=True,
        ),
        flush=True,
    )

    psutil.cpu_percent(interval=None)
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    runtime_rows: list[dict[str, Any]] = []
    cpu_samples: list[float] = []
    ctx = get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        futures = [pool.submit(worker, job) for job in jobs]
        done = 0
        for fut in as_completed(futures):
            result = fut.result()
            done += 1
            cpu_now = float(psutil.cpu_percent(interval=0.25))
            cpu_samples.append(cpu_now)
            row = result["summary"]
            rows.append(row)
            runtime_rows.append(
                {
                    "d_tag_mm": float(result["d_tag_mm"]),
                    "completed": done,
                    "total": len(futures),
                    "wall_s": float(row["wall_s"]),
                    "live_cpu_percent": cpu_now,
                }
            )
            print(
                json.dumps(
                    {
                        "stage": "b_sweep_value_done",
                        "d_tag_mm": float(result["d_tag_mm"]),
                        "done": done,
                        "total": len(futures),
                        "wall_s": round(float(row["wall_s"]), 3),
                        "live_cpu_percent": cpu_now,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    rows = sorted(rows, key=lambda r: float(r["d_tag_mm"]))
    curve = pd.DataFrame(rows)
    best_median = curve.iloc[int(np.nanargmin(curve["median_3d_mm"].to_numpy(dtype=float)))].to_dict()
    best_rmse = curve.iloc[int(np.nanargmin(curve["rmse_3d_mm"].to_numpy(dtype=float)))].to_dict()
    loo_row = row_at_or_near(curve, loo_dtag)
    status = "range_objective_fine" if 45.0 <= float(best_median["d_tag_mm"]) <= 60.0 and 45.0 <= float(best_rmse["d_tag_mm"]) <= 60.0 else "range_objective_suboptimal"
    if 80.0 <= float(best_median["d_tag_mm"]) <= 90.0 or 80.0 <= float(best_rmse["d_tag_mm"]) <= 90.0:
        status = "range_objective_suboptimal_optimum_80_to_90"
    summary = {
        "script": str(THIS),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "grid_values": int(len(grid)),
        "step_mm": float(args.step_mm),
        "loo_dtag_mm": float(loo_dtag),
        "best_median_dtag_mm": float(best_median["d_tag_mm"]),
        "best_median_3d_mm": float(best_median["median_3d_mm"]),
        "best_median_rmse_3d_mm": float(best_median["rmse_3d_mm"]),
        "best_median_abs_vertical_mm": float(best_median["median_abs_vertical_mm"]),
        "best_median_signed_vertical_slope_mm_per_m": float(best_median["signed_vertical_slope_mm_per_m"]),
        "best_rmse_dtag_mm": float(best_rmse["d_tag_mm"]),
        "best_rmse_3d_mm": float(best_rmse["rmse_3d_mm"]),
        "best_rmse_median_3d_mm": float(best_rmse["median_3d_mm"]),
        "best_rmse_abs_vertical_mm": float(best_rmse["median_abs_vertical_mm"]),
        "best_rmse_signed_vertical_slope_mm_per_m": float(best_rmse["signed_vertical_slope_mm_per_m"]),
        "loo_eval_dtag_mm": float(loo_row["d_tag_mm"]),
        "loo_eval_median_3d_mm": float(loo_row["median_3d_mm"]),
        "loo_eval_rmse_3d_mm": float(loo_row["rmse_3d_mm"]),
        "loo_eval_abs_vertical_mm": float(loo_row["median_abs_vertical_mm"]),
        "loo_eval_signed_vertical_slope_mm_per_m": float(loo_row["signed_vertical_slope_mm_per_m"]),
        "verdict": status,
        "physical_cores": int(physical),
        "logical_cores": int(logical),
        "workers": int(workers),
        "elapsed_s": float(time.perf_counter() - started),
        "cpu_percent_mean_live": float(np.nanmean(cpu_samples)) if cpu_samples else float("nan"),
        "cpu_percent_max_live": float(np.nanmax(cpu_samples)) if cpu_samples else float("nan"),
        "source_v5_layout": str(V5_LAYOUT),
        "alignment": "V5 self-cal anchors rigidly aligned to Vicon, scale fixed at 1",
    }
    write_csv(curve_path, rows)
    write_csv(summary_path, [summary])
    write_csv(runtime_path, sorted(runtime_rows, key=lambda r: float(r["d_tag_mm"])))
    print(json.dumps({"status": "ok", "summary": summary}, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
