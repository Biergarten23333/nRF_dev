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
REPO_ROOT = THIS.parents[6]
FULL_ROOT = EXTRA_ROOT / "FULL"
ABLATION_SCRIPT = COMPARISON_ROOT / "scripts/run_static_layout_ablation.py"
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"
LAYOUT_BASE = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check"
V5_LAYOUT = LAYOUT_BASE / "v5-commonmode/layout.json"
SIGMA_PATH = LAYOUT_BASE / "tables/anchor_sigma.json"
PAIR_QUALITY = LAYOUT_BASE / "tables/pair_quality_solve.csv"
STATIC_TABLE = LAYOUT_BASE / "tables/static_all_captures.csv"
CAPTURES_ROOT = OFFICIAL_ROOT / "captures/erlangen_20260528_optitrack"
OPTI_ROOT = OFFICIAL_ROOT / "opti_captures/full"
STATIC_TAG = "BSF66F"
ANCHORS = list("ABCDEFGH")
ORACLE_PROXY_DTAG_MM = 91.153
PER_FRAME_JOINT_DTAG_MEDIAN_MM = 46.7

sys.path.insert(0, str(SOLVER_ROOT))
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.trajectory import solve_capture_trajectory  # noqa: E402


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


def write_layout_json(path: Path, *, name: str, coords: list[list[float]], delays: dict[int, float], tag_delay_mm: float = 0.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {
        "version": name,
        "label": name,
        "anchors": [
            {
                "id": int(aid),
                "label": ANCHORS[int(aid)],
                "x_mm": float(coords[int(aid)][0]),
                "y_mm": float(coords[int(aid)][1]),
                "z_mm": float(coords[int(aid)][2]),
                "d_anchor_mm": float(delays[int(aid)]),
            }
            for aid in range(8)
        ],
        "tag_delay_mm": float(tag_delay_mm),
        "metadata": {"generated_by": str(THIS)},
    }
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def session_id_from_path(path: Path) -> str:
    for parent in path.parents:
        if parent.name.startswith("static_ID"):
            return parent.name.split("_")[1]
    return path.parents[1].name


def precompute_static_median_ranges(static_files: list[Path]) -> tuple[dict[str, dict[int, float]], dict[str, dict[int, int]]]:
    medians: dict[str, dict[int, float]] = {}
    counts: dict[str, dict[int, int]] = {}
    for path in static_files:
        sid = session_id_from_path(path)
        frames = read_tr_all_frames(path, tags={STATIC_TAG}, min_anchors=4)
        by_anchor: dict[int, list[float]] = {aid: [] for aid in range(8)}
        for frame in frames:
            for obs in frame.observations:
                if 0 <= obs.anchor_id < 8 and obs.range_mm > 0.0:
                    by_anchor[obs.anchor_id].append(float(obs.range_mm))
        medians[sid] = {aid: float(np.nanmedian(vals)) for aid, vals in by_anchor.items() if vals}
        counts[sid] = {aid: len(vals) for aid, vals in by_anchor.items() if vals}
    return medians, counts


def fit_fold_dtag(
    *,
    heldout_id: str,
    medians_by_id: dict[str, dict[int, float]],
    tag_truth: dict[str, list[float]],
    anchor_coords: list[list[float]],
    delays: dict[int, float],
) -> tuple[float, int]:
    residuals: list[float] = []
    anchors = np.asarray(anchor_coords, dtype=float)
    for sid, by_anchor in medians_by_id.items():
        if sid == heldout_id or sid not in tag_truth:
            continue
        truth = np.asarray(tag_truth[sid], dtype=float)
        for aid, measured in by_anchor.items():
            geom = float(np.linalg.norm(truth - anchors[int(aid)]))
            residuals.append(float(measured) - geom - float(delays[int(aid)]))
    if not residuals:
        return float("nan"), 0
    return float(np.nanmedian(np.asarray(residuals, dtype=float))), int(len(residuals))


def regression_slope(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    mask = np.isfinite(x) & np.isfinite(y)
    xx = x[mask]
    yy = y[mask]
    if xx.size < 3 or float(np.nanstd(xx)) <= 1e-12:
        return float("nan"), float("nan")
    slope, intercept = np.polyfit(xx, yy, 1)
    pred = slope * xx + intercept
    ss_res = float(np.sum((yy - pred) ** 2))
    ss_tot = float(np.sum((yy - np.mean(yy)) ** 2))
    return float(slope * 1000.0), float(1.0 - ss_res / ss_tot) if ss_tot > 0.0 else float("nan")


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    df = pd.DataFrame(rows)
    out: list[dict[str, Any]] = []
    for (variant, case), g in df.groupby(["variant", "case"], dropna=False):
        err = g["err_3d_mm"].to_numpy(dtype=float)
        truth_y = g["truth_y_vertical_mm"].to_numpy(dtype=float)
        signed_y = g["err_y_vertical_mm"].to_numpy(dtype=float)
        slope, r2 = regression_slope(truth_y, signed_y)
        dtag_vals = g["d_tag_fit_mm"].to_numpy(dtype=float)
        out.append(
            {
                "variant": variant,
                "case": case,
                "n_folds": int(len(g)),
                "heldout_median_3d_mm": float(np.nanmedian(err)),
                "heldout_p95_3d_mm": float(np.nanpercentile(err, 95)),
                "heldout_rmse_3d_mm": float(math.sqrt(np.nanmean(err * err))),
                "heldout_median_abs_vertical_mm": float(np.nanmedian(g["err_vertical_y_mm"].to_numpy(dtype=float))),
                "signed_vertical_slope_mm_per_m": slope,
                "signed_vertical_r2": r2,
                "d_tag_median_mm": float(np.nanmedian(dtag_vals)),
                "d_tag_p25_mm": float(np.nanpercentile(dtag_vals, 25)),
                "d_tag_p75_mm": float(np.nanpercentile(dtag_vals, 75)),
                "d_tag_min_mm": float(np.nanmin(dtag_vals)),
                "d_tag_max_mm": float(np.nanmax(dtag_vals)),
            }
        )
    return out


def worker(job: dict[str, Any]) -> dict[str, Any]:
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    t0 = time.perf_counter()
    ablation = load_module(Path(job["ablation_script"]), f"loo_ablation_helpers_{os.getpid()}")
    tag_truth = {k: np.asarray(v, dtype=float) for k, v in job["tag_truth"].items()}
    tag_truth_meta = job["tag_truth_meta"]
    metadata_by_id = job["metadata_by_id"]
    heldout_id = job["heldout_id"]
    heldout_path = Path(job["heldout_path"])
    fold_layout_dir = Path(job["fold_layout_dir"])
    rows: list[dict[str, Any]] = []
    dtag_by_variant: dict[str, float] = {}
    for variant in job["variants"]:
        delays = {int(k): float(v) for k, v in variant["delays"].items()}
        dtag, n_terms = fit_fold_dtag(
            heldout_id=heldout_id,
            medians_by_id=job["medians_by_id"],
            tag_truth=job["tag_truth"],
            anchor_coords=variant["coords"],
            delays=delays,
        )
        dtag_by_variant[variant["name"]] = dtag
        for case, effective_dtag in [("zero_tag", 0.0), ("loo_tag_delay", dtag)]:
            layout_path = fold_layout_dir / f"{heldout_id}_{variant['name']}_{case}.json"
            write_layout_json(
                layout_path,
                name=f"{variant['name']}_{case}_{heldout_id}",
                coords=variant["coords"],
                delays=delays,
                tag_delay_mm=0.0,
            )
            traj = solve_capture_trajectory(
                layout_path,
                heldout_path,
                method="T4",
                anchor_sigma_path=job["sigma_path"],
                tags={STATIC_TAG},
                tag_delay_by_tag={STATIC_TAG: float(effective_dtag)},
            )
            row = ablation.static_error_row_from_results(
                heldout_path,
                traj.results,
                tag_truth=tag_truth,
                tag_truth_meta=tag_truth_meta,
                metadata_by_id=metadata_by_id,
                metadata={
                    "variant": variant["name"],
                    "case": case,
                    "fold": heldout_id,
                    "d_tag_fit_mm": float(dtag),
                    "d_tag_eval_mm": float(effective_dtag),
                    "d_tag_fit_terms": int(n_terms),
                    "layout_frame": variant["layout_frame"],
                    "delay_source": variant["delay_source"],
                    "layout_json": str(layout_path),
                    "source_v5_layout": job["v5_layout"],
                },
                tag_method="T4",
                point_estimator="mean",
                frames_input=traj.frames_input,
            )
            if row is not None:
                row["source_tr_all"] = str(heldout_path)
                rows.append(row)
    return {
        "heldout_id": heldout_id,
        "rows": rows,
        "wall_s": time.perf_counter() - t0,
        "dtag_by_variant": dtag_by_variant,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Leave-one-position-out tag-delay calibration on the V5 common-mode layout.")
    parser.add_argument("--out-dir", type=Path, default=COMPARISON_ROOT / "tables")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    tables = args.out_dir.resolve()
    per_fold_path = tables / "v5_loo_tag_delay_per_fold.csv"
    summary_path = tables / "v5_loo_tag_delay_summary.csv"
    runtime_path = tables / "v5_loo_tag_delay_runtime.csv"
    if not args.replace:
        for path in (per_fold_path, summary_path, runtime_path):
            if path.exists():
                raise SystemExit(f"refusing to overwrite existing output without --replace: {path}")
    tables.mkdir(parents=True, exist_ok=True)
    fold_layout_dir = tables.parent / f"v5_loo_fold_layouts_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    fold_layout_dir.mkdir(parents=True, exist_ok=False)

    if not V5_LAYOUT.exists():
        raise FileNotFoundError(f"missing V5 layout; run S1 first: {V5_LAYOUT}")
    ablation = load_module(ABLATION_SCRIPT, "loo_ablation_helpers_main")
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
    sim3 = ablation.fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=True)
    coords_rigid = ablation.apply_fit(src, rigid)
    coords_sim3 = ablation.apply_fit(src, sim3)
    delaycal_delays, delaycal_tag_delay, _delay_rows = ablation.estimate_delaycal(anchor_truth, PAIR_QUALITY)
    static_files = sorted(CAPTURES_ROOT.glob("static_ID*/tag_capture*/tr_all.csv"))
    static_by_id = {session_id_from_path(p): p for p in static_files}
    medians_by_id, median_counts = precompute_static_median_ranges(static_files)
    metadata_by_id = ablation.load_static_metadata(STATIC_TABLE)
    tag_truth = {k: v.tolist() for k, v in tag_truth_np.items()}

    variants = [
        {
            "name": "deployable_v5_rigid_selfcal",
            "coords": coords_rigid.tolist(),
            "delays": {int(k): float(v) for k, v in v5_delays.items()},
            "layout_frame": "V5 self-cal anchors rigidly aligned to Vicon, scale fixed at 1",
            "delay_source": "V5 layout d_anchor_mm",
        },
        {
            "name": "physical_sim3_vicon_delaycal",
            "coords": coords_sim3.tolist(),
            "delays": {int(k): float(v) for k, v in delaycal_delays.items()},
            "layout_frame": "V5 anchors full-Sim3 transformed to Vicon frame",
            "delay_source": "Vicon inter-anchor delaycal endpoint delays",
        },
    ]

    physical = psutil.cpu_count(logical=False) or 6
    logical = psutil.cpu_count(logical=True) or physical
    workers = max(1, min(int(args.workers), len(static_by_id), physical))
    jobs = [
        {
            "heldout_id": sid,
            "heldout_path": str(static_by_id[sid]),
            "variants": variants,
            "medians_by_id": medians_by_id,
            "median_counts": median_counts,
            "tag_truth": tag_truth,
            "tag_truth_meta": tag_truth_meta,
            "metadata_by_id": metadata_by_id,
            "sigma_path": str(SIGMA_PATH),
            "fold_layout_dir": str(fold_layout_dir),
            "ablation_script": str(ABLATION_SCRIPT),
            "v5_layout": str(V5_LAYOUT),
        }
        for sid in sorted(static_by_id)
        if sid in tag_truth
    ]
    print(
        json.dumps(
            {
                "stage": "s5_start",
                "physical_cores": physical,
                "logical_cores": logical,
                "workers": workers,
                "folds": len(jobs),
                "blas_threads_per_worker": {k: os.environ.get(k) for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")},
            },
            sort_keys=True,
        ),
        flush=True,
    )
    psutil.cpu_percent(interval=None)
    all_rows: list[dict[str, Any]] = []
    runtime_rows: list[dict[str, Any]] = []
    cpu_samples: list[float] = []
    started = time.perf_counter()
    ctx = get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        futures = [pool.submit(worker, job) for job in jobs]
        done = 0
        for fut in as_completed(futures):
            result = fut.result()
            done += 1
            all_rows.extend(result["rows"])
            cpu_now = float(psutil.cpu_percent(interval=0.25))
            cpu_samples.append(cpu_now)
            runtime_rows.append(
                {
                    "fold": result["heldout_id"],
                    "wall_s": float(result["wall_s"]),
                    "completed": done,
                    "total": len(futures),
                    "live_cpu_percent": cpu_now,
                    "dtag_by_variant": json.dumps(result["dtag_by_variant"], sort_keys=True),
                }
            )
            print(
                json.dumps(
                    {
                        "stage": "s5_fold_done",
                        "fold": result["heldout_id"],
                        "done": done,
                        "total": len(futures),
                        "wall_s": round(float(result["wall_s"]), 3),
                        "live_cpu_percent": cpu_now,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    summary_rows = summarize(all_rows)
    gz_path = FULL_ROOT / "tables/gz_bias_sensitivity_summary.csv"
    gz_slope = float("nan")
    gz_r2 = float("nan")
    if gz_path.exists():
        gz_df = pd.read_csv(gz_path)
        if not gz_df.empty:
            gz_slope = float(gz_df.iloc[0].get("gz_layout_slope_missing_dtag_mm", float("nan")))
            gz_r2 = float(gz_df.iloc[0].get("gz_layout_r2", float("nan")))
    for row in summary_rows:
        row["gz_slope_missing_dtag_mm"] = gz_slope
        row["gz_slope_r2"] = gz_r2
        row["per_frame_joint_dtag_median_mm"] = PER_FRAME_JOINT_DTAG_MEDIAN_MM
        row["proxy_oracle_dtag_mm"] = ORACLE_PROXY_DTAG_MM
        row["vicon_delaycal_tag_delay_from_interanchor_mm"] = float(delaycal_tag_delay)
        row["physical_cores"] = physical
        row["logical_cores"] = logical
        row["workers"] = workers
        row["elapsed_s"] = float(time.perf_counter() - started)
        row["cpu_percent_mean_live"] = float(np.nanmean(cpu_samples)) if cpu_samples else float("nan")
        row["cpu_percent_max_live"] = float(np.nanmax(cpu_samples)) if cpu_samples else float("nan")
        row["fold_layout_dir"] = str(fold_layout_dir)

    write_csv(per_fold_path, all_rows)
    write_csv(summary_path, summary_rows)
    write_csv(runtime_path, runtime_rows)
    print(
        json.dumps(
            {
                "status": "ok",
                "per_fold": str(per_fold_path),
                "summary": str(summary_path),
                "runtime": str(runtime_path),
                "summary_rows": summary_rows,
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
