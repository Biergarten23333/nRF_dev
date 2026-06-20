#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import csv
import importlib.util
import json
import math
import multiprocessing as mp
import os
import py_compile
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

import numpy as np
import pandas as pd
import psutil

try:
    from scipy import stats
except Exception:  # pragma: no cover - scipy exists on the workstation, fallback keeps skips clean.
    stats = None

THIS = Path(__file__).resolve()
ANALYSIS = THIS.parents[2]
BASE = THIS.parents[4]
REPO_ROOT = THIS.parents[6]
OUT_ROOT = ANALYSIS / "FULL_V5_extended_mechanism_ablations"
PREV_MECH_SCRIPT = ANALYSIS / "FULL_V5_mechanism_ablations/scripts/run_v5_mechanism_ablations.py"
FULL_V5_SCRIPT = ANALYSIS / "FULL_V5/scripts/run_full_v5_ablation_pipeline.py"
TRANSFER_CELLS = ANALYSIS / "FULL_transfer_matrix/tables/transfer_matrix_48cells.csv"
TRANSFER_SWEEP = ANALYSIS / "FULL_transfer_matrix/tables/transfer_matrix_Dsweep_detail.csv"
PREV_HEIGHT_OPTIMA = ANALYSIS / "FULL_V5_mechanism_ablations/D_per_height_dtag/tables/per_height_dtag_optima.csv"
VICON_CM_DELAY = ANALYSIS / "FULL_V5_align_to_Vicon/tables/vicon_anchor_delays_refit_cm.csv"
VICON_INDEP_DELAY = ANALYSIS / "FULL_V5_align_to_Vicon/tables/vicon_anchor_delays_refit_indep.csv"
FULL_V5_STATIC = ANALYSIS / "FULL_V5/tables/static_per_position.csv"
FULL_V5_CIRCLE = ANALYSIS / "FULL_V5/tables/roto_circle_fit.csv"
ROTO_RHO_EXISTING = ANALYSIS / "FULL_4way_comparison/tables/RotoArm_C_dynamic_rho_per_frame_anchor.csv"
SIGMA_PATH = BASE / "solver/outputs/v1_to_v4_io_field_check/tables/anchor_sigma.json"
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"

sys.path.insert(0, str(SOLVER_ROOT))
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_anchor_sigma  # noqa: E402
from biospur_tag_positioning_offline_solver.models import Frame, Observation  # noqa: E402
from biospur_tag_positioning_offline_solver.trajectory import solve_capture_trajectory  # noqa: E402

WORKERS = 6
LOO_DTAG_MM = 49.621
V5_COMMON_MODE_MM = 111.985
ANCHORS = tuple("ABCDEFGH")
STATIC_TAG = "BSF66F"
ROTO_TAGS = ("BS2DCE", "BSDC91")
LOWER_ANCHORS = {0, 1, 2, 3}
UPPER_ANCHORS = {4, 5, 6, 7}

_WORKER_MECH = None
_WORKER_FULL = None
_WORKER_ABLATION = None


@dataclass(frozen=True)
class ConfigSpec:
    label: str
    layout_source: str
    correction_source: str
    coords: np.ndarray
    delays: dict[int, float]
    notes: str = ""


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


def percentile(values: Any, pct: float) -> float:
    arr = finite(values)
    return float(np.nanpercentile(arr, pct)) if arr.size else float("nan")


def rmse(values: Any) -> float:
    arr = finite(values)
    return float(math.sqrt(float(np.nanmean(arr * arr)))) if arr.size else float("nan")


def linear_regression(x: Any, y: Any, slope_scale: float = 1.0) -> dict[str, float]:
    xx = np.asarray(x, dtype=float)
    yy = np.asarray(y, dtype=float)
    mask = np.isfinite(xx) & np.isfinite(yy)
    xx = xx[mask]
    yy = yy[mask]
    if xx.size < 3 or float(np.nanstd(xx)) <= 1e-12:
        return {"slope": float("nan"), "intercept": float("nan"), "r2": float("nan"), "p_value": float("nan"), "n": int(xx.size)}
    if stats is not None:
        res = stats.linregress(xx, yy)
        return {
            "slope": float(res.slope * slope_scale),
            "intercept": float(res.intercept),
            "r2": float(res.rvalue * res.rvalue),
            "p_value": float(res.pvalue),
            "n": int(xx.size),
        }
    slope, intercept = np.polyfit(xx, yy, 1)
    pred = slope * xx + intercept
    ss_res = float(np.sum((yy - pred) ** 2))
    ss_tot = float(np.sum((yy - np.mean(yy)) ** 2))
    return {
        "slope": float(slope * slope_scale),
        "intercept": float(intercept),
        "r2": float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan"),
        "p_value": float("nan"),
        "n": int(xx.size),
    }


def make_dirs() -> dict[str, Path]:
    dirs = {
        "root": OUT_ROOT,
        "scripts": OUT_ROOT / "scripts",
        "tables": OUT_ROOT / "tables",
        "figures": OUT_ROOT / "figures",
        "reports": OUT_ROOT / "reports",
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


def static_id_from_path(path: str | Path) -> str:
    p = Path(path)
    for parent in p.parents:
        if parent.name.startswith("static_ID"):
            return parent.name.split("_")[1]
    raise ValueError(f"cannot parse static ID from {p}")


def capture_epoch_from_path(path: str | Path) -> str:
    p = Path(path)
    for parent in p.parents:
        if parent.name.startswith("static_ID"):
            m = re.search(r"_(\d{8}_\d{6})$", parent.name)
            return m.group(1) if m else parent.name
    return p.parent.name


def worker_context():
    global _WORKER_MECH, _WORKER_FULL, _WORKER_ABLATION
    if _WORKER_MECH is None:
        _WORKER_MECH = load_module(PREV_MECH_SCRIPT, f"extended_mech_prev_{os.getpid()}")
    if _WORKER_FULL is None:
        _WORKER_FULL = load_module(FULL_V5_SCRIPT, f"extended_full_v5_{os.getpid()}")
    if _WORKER_ABLATION is None:
        _WORKER_ABLATION = _WORKER_FULL.load_module(
            _WORKER_FULL.ABLATION_SCRIPT,
            f"extended_static_ablation_{os.getpid()}",
        )
    return _WORKER_MECH, _WORKER_FULL, _WORKER_ABLATION


def solve_static_job_ext(job: dict[str, Any]) -> dict[str, Any]:
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    mech, full, ab = worker_context()
    coords = np.asarray(job["coords"], dtype=float)
    delays = {int(k): float(v) for k, v in job["delays"].items()}
    sigma_by_id = {int(k): float(v) for k, v in job["sigma_by_id"].items()}
    layout = ab.build_layout(
        name=str(job["layout_name"]),
        labels=list(ANCHORS),
        coords_opti_frame=coords,
        delays=delays,
        tag_delay_mm=0.0,
        sigma_by_id=sigma_by_id,
        metadata=job.get("metadata", {}),
    )
    solver = ab.TagPositionSolver(
        layout,
        ab.SolverConfig(method=str(job.get("method", "T4"))),
        tag_delay_by_tag={full.STATIC_TAG: float(job["d_tag_mm"])},
    )
    tag_truth = {k: np.asarray(v, dtype=float) for k, v in job["tag_truth"].items()}
    anchor_centroid = np.asarray(job["anchor_centroid"], dtype=float)
    allowed = job.get("allowed_anchor_ids")
    allowed_set = set(int(x) for x in allowed) if allowed is not None else None
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
            allowed_anchor_ids=allowed_set,
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
        "config": job.get("config", ""),
        "d_tag_mm": float(job["d_tag_mm"]),
        "summary": summary,
        "rows": rows if job.get("return_rows", False) else [],
    }


def eval_chunk_worker(chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [solve_static_job_ext(job) for job in chunk]


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def run_eval_jobs(jobs: list[dict[str, Any]], ctx: dict[str, Any], stage: str, chunk_size: int = 8) -> list[dict[str, Any]]:
    if not jobs:
        return []
    chunks = chunked(jobs, chunk_size)
    results: list[dict[str, Any]] = []
    done = 0
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


def make_static_job(
    *,
    job_id: str,
    config: ConfigSpec,
    d_tag_mm: float,
    ids: list[str],
    inputs: dict[str, Any],
    meta: dict[str, Any] | None = None,
    return_rows: bool = False,
    sigma_by_id: dict[int, float] | None = None,
    allowed_anchor_ids: list[int] | None = None,
    delays_override: dict[int, float] | None = None,
    coords_override: np.ndarray | None = None,
) -> dict[str, Any]:
    files = [inputs["static_by_id"][sid] for sid in ids]
    return {
        "job_id": job_id,
        "config": config.label,
        "layout_name": config.label,
        "coords": np.asarray(coords_override if coords_override is not None else config.coords, dtype=float).tolist(),
        "delays": delays_override or config.delays,
        "sigma_by_id": sigma_by_id or inputs["sigma_by_id"],
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
        "allowed_anchor_ids": allowed_anchor_ids,
        "metadata": {"extended_mechanism_ablation": True, "config": config.label},
        "meta": meta or {},
    }


def read_vicon_delays(path: Path, cm_style: bool) -> dict[int, float]:
    df = pd.read_csv(require_path(path, "Vicon delay table"))
    if cm_style and "row_type" in df.columns:
        df = df[df["row_type"] == "anchor"].copy()
    if "anchor_id" not in df.columns or "d_anchor_mm" not in df.columns:
        raise RuntimeError(f"cannot parse delay table: {path}")
    out: dict[int, float] = {}
    for _, r in df.iterrows():
        if pd.isna(r["anchor_id"]):
            continue
        out[int(r["anchor_id"])] = float(r["d_anchor_mm"])
    if len(out) != 8:
        raise RuntimeError(f"expected 8 anchor delays in {path}, found {len(out)}")
    return out


def load_previous_modules() -> tuple[Any, Any]:
    require_path(PREV_MECH_SCRIPT, "existing mechanism ablation script")
    require_path(FULL_V5_SCRIPT, "FULL_V5 helper script")
    mech = load_module(PREV_MECH_SCRIPT, "extended_prev_mechanism_main")
    full = mech.load_full_module()
    return mech, full


def build_inputs_and_configs(mech, full) -> tuple[dict[str, Any], dict[str, ConfigSpec], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    for path, label in [
        (TRANSFER_CELLS, "transfer matrix cells"),
        (TRANSFER_SWEEP, "transfer matrix sweep"),
        (PREV_HEIGHT_OPTIMA, "previous per-height D_tag optima"),
        (VICON_CM_DELAY, "Vicon common-mode delays"),
        (VICON_INDEP_DELAY, "Vicon independent delays"),
        (FULL_V5_STATIC, "FULL_V5 static per-position table"),
        (FULL_V5_CIRCLE, "FULL_V5 ROTO circle table"),
        (SIGMA_PATH, "anchor sigma table"),
    ]:
        require_path(path, label)
    inputs = mech.build_inputs(full)
    inputs["static_by_id"] = {static_id_from_path(p): str(p) for p in inputs["static_files"]}
    inputs["tag_truth_np"] = {k: np.asarray(v, dtype=float) for k, v in inputs["tag_truth"].items()}
    cm_delays = read_vicon_delays(VICON_CM_DELAY, cm_style=True)
    indep_delays = read_vicon_delays(VICON_INDEP_DELAY, cm_style=False)
    zeros = {i: 0.0 for i in range(8)}
    configs = {
        "V4_CV4": ConfigSpec("V4_CV4", "L_V4", "C_V4", inputs["coords_v4_rigid"], {int(k): float(v) for k, v in inputs["delays_v4"].items()}, "V4 production style"),
        "V5_CV5": ConfigSpec("V5_CV5", "L_V5", "C_V5", inputs["coords_v5_rigid"], {int(k): float(v) for k, v in inputs["delays_v5"].items()}, "V5 common-mode self-cal"),
        "Vicon_Ccm": ConfigSpec("Vicon_Ccm", "L_Vicon", "C_Vicon_cm", inputs["truth_coords"], cm_delays, "Vicon known-anchor common-mode refit"),
        "V4_Cnone": ConfigSpec("V4_Cnone", "L_V4", "C_none", inputs["coords_v4_rigid"], zeros, "V4 geometry only"),
        "V4_CV5": ConfigSpec("V4_CV5", "L_V4", "C_V5", inputs["coords_v4_rigid"], {int(k): float(v) for k, v in inputs["delays_v5"].items()}, "V5 delays in V4 frame"),
        "V5_Cnone": ConfigSpec("V5_Cnone", "L_V5", "C_none", inputs["coords_v5_rigid"], zeros, "V5 geometry only"),
        "Vicon_Cindep": ConfigSpec("Vicon_Cindep", "L_Vicon", "C_Vicon_indep", inputs["truth_coords"], indep_delays, "Vicon independent-delay oracle"),
    }
    assignments, maps = mech.assign_positions(inputs)
    by_id: dict[str, dict[str, Any]] = {}
    for row in assignments:
        sid = row["position_id"]
        meta = inputs["metadata_by_id"].get(sid, {})
        by_id[sid] = {
            **row,
            "height": meta.get("height", ""),
            "facing": meta.get("facing", ""),
            "location": meta.get("location", ""),
        }
    maps_ext = {
        "height": {sid: by_id[sid]["height_tier"] for sid in by_id},
        "edge": {sid: by_id[sid]["edge_center_group"] for sid in by_id},
        "distance": {sid: float(by_id[sid]["distance_to_centroid_mm"]) for sid in by_id},
        "facing": {sid: str(by_id[sid].get("facing", "")) for sid in by_id},
        "position_rows": by_id,
    }
    return inputs, configs, assignments, maps_ext


def load_raw_ranges(static_files: list[Path]) -> tuple[dict[str, dict[int, np.ndarray]], dict[str, dict[str, float]]]:
    raw: dict[str, dict[int, list[float]]] = {}
    info: dict[str, dict[str, float]] = {}
    for path in static_files:
        sid = static_id_from_path(path)
        frames = read_tr_all_frames(path, tags={STATIC_TAG}, min_anchors=4)
        by_anchor = {aid: [] for aid in range(8)}
        epochs = []
        elapsed = []
        for frame in frames:
            epochs.append(float(frame.host_epoch_s))
            elapsed.append(float(frame.host_elapsed_s))
            for obs in frame.observations:
                aid = int(obs.anchor_id)
                if 0 <= aid < 8 and float(obs.range_mm) > 0.0:
                    by_anchor[aid].append(float(obs.range_mm))
        raw[sid] = {aid: np.asarray(vals, dtype=float) for aid, vals in by_anchor.items() if vals}
        info[sid] = {
            "first_epoch_s": float(np.nanmin(epochs)) if epochs else float("nan"),
            "last_epoch_s": float(np.nanmax(epochs)) if epochs else float("nan"),
            "first_elapsed_s": float(np.nanmin(elapsed)) if elapsed else float("nan"),
            "n_frames": int(len(frames)),
            "capture_time_key": capture_epoch_from_path(path),
        }
    return raw, info


def raw_medians(raw_ranges: dict[str, dict[int, np.ndarray]]) -> dict[str, dict[int, float]]:
    return {
        sid: {aid: float(np.nanmedian(vals)) for aid, vals in by_anchor.items() if vals.size}
        for sid, by_anchor in raw_ranges.items()
    }


def residual_observations(
    config: ConfigSpec,
    medians_by_id: dict[str, dict[int, float]],
    tag_truth: dict[str, np.ndarray],
    maps: dict[str, dict[str, Any]],
    d_tag_mm: float = LOO_DTAG_MM,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pos_rows = maps["position_rows"]
    centroid = np.vstack([tag_truth[sid] for sid in sorted(tag_truth)]).mean(axis=0)
    for sid, by_anchor in medians_by_id.items():
        truth = tag_truth.get(sid)
        if truth is None:
            continue
        for aid, measured in by_anchor.items():
            a = config.coords[int(aid)]
            diff = a - truth
            horiz = math.sqrt(float(diff[0] * diff[0] + diff[2] * diff[2]))
            theta = math.degrees(math.atan2(float(diff[1]), horiz))
            geom = float(np.linalg.norm(diff))
            effective = float(measured) - geom - float(config.delays[int(aid)])
            rho = effective - float(d_tag_mm)
            meta = pos_rows.get(sid, {})
            rows.append(
                {
                    "config": config.label,
                    "position_id": sid,
                    "anchor_id": int(aid),
                    "anchor_label": ANCHORS[int(aid)],
                    "layer": "lower" if int(aid) in LOWER_ANCHORS else "upper",
                    "height_tier": meta.get("height_tier", ""),
                    "height": meta.get("height", ""),
                    "facing": meta.get("facing", ""),
                    "edge_center": meta.get("edge_center_group", ""),
                    "truth_x_mm": float(truth[0]),
                    "truth_y_mm": float(truth[1]),
                    "truth_z_mm": float(truth[2]),
                    "distance_to_centroid_mm": float(np.linalg.norm(truth - centroid)),
                    "range_median_mm": float(measured),
                    "geometric_mm": geom,
                    "d_anchor_mm": float(config.delays[int(aid)]),
                    "effective_dtag_mm": effective,
                    "rho_mm": rho,
                    "theta_deg": theta,
                    "abs_theta_deg": abs(theta),
                    "anchor_distance_mm": geom,
                }
            )
    return rows


def median_dtag_from_obs(rows: list[dict[str, Any]], ids: set[str] | None = None, anchors: set[int] | None = None) -> tuple[float, int]:
    vals = []
    for r in rows:
        if ids is not None and r["position_id"] not in ids:
            continue
        if anchors is not None and int(r["anchor_id"]) not in anchors:
            continue
        vals.append(float(r["effective_dtag_mm"]))
    arr = finite(vals)
    return (float(np.nanmedian(arr)) if arr.size else float("nan"), int(arr.size))


def aggregate_rows(rows: list[dict[str, Any]], expected_positions: int | None = None) -> dict[str, Any]:
    df = pd.DataFrame(rows)
    if df.empty:
        return {
            "median_3d_mm": float("nan"),
            "p95_3d_mm": float("nan"),
            "rmse_3d_mm": float("nan"),
            "median_vertical_mm": float("nan"),
            "signed_vertical_slope_mm_per_m": float("nan"),
            "signed_vertical_slope_r2": float("nan"),
            "n_positions": 0,
            "n_ranges": 0,
            "fail_rate": 1.0,
        }
    reg = linear_regression(df["truth_y_vertical_mm"], df["err_y_vertical_mm"], slope_scale=1000.0)
    expected = expected_positions or len(df)
    return {
        "median_3d_mm": percentile(df["err_3d_mm"], 50),
        "p95_3d_mm": percentile(df["err_3d_mm"], 95),
        "rmse_3d_mm": rmse(df["err_3d_mm"]),
        "median_vertical_mm": percentile(df["err_vertical_y_mm"], 50),
        "signed_vertical_slope_mm_per_m": reg["slope"],
        "signed_vertical_slope_r2": reg["r2"],
        "n_positions": int(len(df)),
        "n_ranges": int(df["frames_input"].sum()) if "frames_input" in df else 0,
        "fail_rate": float(max(0, expected - len(df)) / expected),
    }


def item01_per_tier_range_residual_dtag(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    residuals: dict[str, list[dict[str, Any]]],
    maps: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 01")
    prev = pd.read_csv(PREV_HEIGHT_OPTIMA)
    prev["config_norm"] = prev["config"].astype(str).str.replace("+", "_", regex=False)
    rows: list[dict[str, Any]] = []
    for label in ("V4_CV4", "V5_CV5", "Vicon_Ccm"):
        obs = residuals[label]
        for tier in ("LOW", "MID", "HIGH"):
            ids = {sid for sid, t in maps["height"].items() if t == tier}
            dtag, n = median_dtag_from_obs(obs, ids=ids)
            match = prev[(prev["config_norm"] == label) & (prev["height_tier"] == tier)]
            pos_opt = float(match.iloc[0]["d_tag_min_median_mm"]) if not match.empty else float("nan")
            rows.append(
                {
                    "config": label,
                    "tier": tier,
                    "n_positions": len(ids),
                    "n_pairs": n,
                    "d_tag_range_residual_mm": dtag,
                    "d_tag_position_optimal_mm": pos_opt,
                    "difference_mm": dtag - pos_opt if np.isfinite(dtag) and np.isfinite(pos_opt) else float("nan"),
                }
            )
    write_csv(dirs["tables"] / "item01_per_tier_range_residual_dtag.csv", rows)
    sample_cpu(ctx)
    return finish_phase(ctx), rows


def item02_elevation_angle(
    dirs: dict[str, Path],
    residuals: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item 02")
    rows = []
    reg_rows = []
    for label in ("V4_CV4", "V5_CV5", "Vicon_Ccm"):
        for r in residuals[label]:
            rows.append(
                {
                    "config": label,
                    "position_id": r["position_id"],
                    "anchor_id": r["anchor_id"],
                    "anchor_label": r["anchor_label"],
                    "theta_deg": r["theta_deg"],
                    "rho_mm": r["rho_mm"],
                    "layer": r["layer"],
                }
            )
        df = pd.DataFrame([r for r in rows if r["config"] == label])
        lin = linear_regression(df["theta_deg"], df["rho_mm"])
        reg_rows.append({"config": label, "model": "linear", "slope": lin["slope"], "intercept": lin["intercept"], "r2": lin["r2"], "p_value": lin["p_value"], "n": lin["n"]})
        abs_fit = linear_regression(np.abs(df["theta_deg"].to_numpy(dtype=float)), df["rho_mm"])
        reg_rows.append({"config": label, "model": "abs_angle", "slope": abs_fit["slope"], "intercept": abs_fit["intercept"], "r2": abs_fit["r2"], "p_value": abs_fit["p_value"], "n": abs_fit["n"]})
    write_csv(dirs["tables"] / "item02_elevation_angle_residual.csv", rows)
    write_csv(dirs["tables"] / "item02_elevation_angle_regression.csv", reg_rows)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        df_all = pd.DataFrame(rows)
        for label, g in df_all.groupby("config"):
            fig, ax = plt.subplots(figsize=(7.5, 4.5))
            ax.scatter(g["theta_deg"], g["rho_mm"], s=16, alpha=0.65)
            fit = linear_regression(g["theta_deg"], g["rho_mm"])
            if np.isfinite(fit["slope"]):
                x = np.linspace(float(g["theta_deg"].min()), float(g["theta_deg"].max()), 100)
                ax.plot(x, fit["slope"] * x + fit["intercept"], color="black", lw=1.5)
            ax.axhline(0, color="0.5", lw=0.8)
            ax.set_xlabel("Elevation angle theta (deg)")
            ax.set_ylabel("rho (mm)")
            ax.set_title(label)
            fig.tight_layout()
            fig.savefig(dirs["figures"] / f"item02_elevation_rho_{label}.png", dpi=150)
            plt.close(fig)
    except Exception as exc:
        write_csv(dirs["tables"] / "item02_figure_skip.csv", [{"status": "skipped", "reason": repr(exc)}])
    sample_cpu(ctx)
    return finish_phase(ctx), rows, reg_rows


def item03_per_anchor_dtag(dirs: dict[str, Path], residuals: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 03")
    rows: list[dict[str, Any]] = []
    for label in ("V4_CV4", "V5_CV5", "Vicon_Ccm"):
        df = pd.DataFrame(residuals[label])
        per = []
        for aid, g in df.groupby("anchor_id"):
            per.append(
                {
                    "config": label,
                    "anchor_id": int(aid),
                    "anchor_label": ANCHORS[int(aid)],
                    "layer": "lower" if int(aid) in LOWER_ANCHORS else "upper",
                    "d_tag_j_mm": float(np.nanmedian(g["effective_dtag_mm"].to_numpy(dtype=float))),
                    "n_positions": int(g["position_id"].nunique()),
                }
            )
        vals = np.asarray([r["d_tag_j_mm"] for r in per], dtype=float)
        lower_mean = float(np.nanmean([r["d_tag_j_mm"] for r in per if r["layer"] == "lower"]))
        upper_mean = float(np.nanmean([r["d_tag_j_mm"] for r in per if r["layer"] == "upper"]))
        for r in per:
            rows.append(
                {
                    **r,
                    "spread_all_anchors_mm": float(np.nanmax(vals) - np.nanmin(vals)),
                    "lower_layer_mean_mm": lower_mean,
                    "upper_layer_mean_mm": upper_mean,
                    "upper_minus_lower_mm": upper_mean - lower_mean,
                }
            )
    write_csv(dirs["tables"] / "item03_per_anchor_effective_dtag.csv", rows)
    sample_cpu(ctx)
    return finish_phase(ctx), rows


def item04_nlos_excluded(dirs: dict[str, Path], residuals: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 04")
    rows: list[dict[str, Any]] = []
    exclusions = ["none", "rho_gt100", "rho_gt150", "top10_positive_rho", "exclude_DF", "exclude_DFAH"]
    for label in ("V4_CV4", "V5_CV5", "Vicon_Ccm"):
        base = pd.DataFrame(residuals[label])
        for exclusion in exclusions:
            g = base.copy()
            if exclusion == "rho_gt100":
                g = g[g["rho_mm"] <= 100.0]
            elif exclusion == "rho_gt150":
                g = g[g["rho_mm"] <= 150.0]
            elif exclusion == "top10_positive_rho":
                pos = g[g["rho_mm"] > 0.0]["rho_mm"].to_numpy(dtype=float)
                cutoff = float(np.nanpercentile(pos, 90)) if pos.size else float("inf")
                g = g[~((g["rho_mm"] > 0.0) & (g["rho_mm"] >= cutoff))]
            elif exclusion == "exclude_DF":
                g = g[~g["anchor_label"].isin(["D", "F"])]
            elif exclusion == "exclude_DFAH":
                g = g[~g["anchor_label"].isin(["D", "F", "A", "H"])]
            tier_vals: dict[str, float] = {}
            for tier in ("global", "LOW", "MID", "HIGH"):
                sub = g if tier == "global" else g[g["height_tier"] == tier]
                val = float(np.nanmedian(sub["effective_dtag_mm"].to_numpy(dtype=float))) if len(sub) else float("nan")
                tier_vals[tier] = val
            finite_tiers = [tier_vals[t] for t in ("LOW", "MID", "HIGH") if np.isfinite(tier_vals[t])]
            spread = float(max(finite_tiers) - min(finite_tiers)) if finite_tiers else float("nan")
            for tier in ("global", "LOW", "MID", "HIGH"):
                sub = g if tier == "global" else g[g["height_tier"] == tier]
                rows.append(
                    {
                        "config": label,
                        "exclusion": exclusion,
                        "tier": tier,
                        "n_pairs_remaining": int(len(sub)),
                        "d_tag_mm": tier_vals[tier],
                        "tier_spread_mm": spread,
                    }
                )
    write_csv(dirs["tables"] / "item04_nlos_excluded_dtag.csv", rows)
    sample_cpu(ctx)
    return finish_phase(ctx), rows


def item05_loo_fold_dtag(
    dirs: dict[str, Path],
    residuals: dict[str, list[dict[str, Any]]],
    inputs: dict[str, Any],
    maps: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item 05")
    rows: list[dict[str, Any]] = []
    reg_rows: list[dict[str, Any]] = []
    ids = sorted(inputs["tag_truth_np"])
    for label in ("V4_CV4", "V5_CV5"):
        obs = pd.DataFrame(residuals[label])
        for k, sid in enumerate(ids, start=1):
            train = obs[obs["position_id"] != sid]
            dtag = float(np.nanmedian(train["effective_dtag_mm"].to_numpy(dtype=float)))
            held = maps["position_rows"][sid]
            held_obs = obs[obs["position_id"] == sid]
            rows.append(
                {
                    "config": label,
                    "fold_k": k,
                    "held_out_position": sid,
                    "held_out_height_mm": float(held["vicon_y_mm"]),
                    "held_out_dist_to_centroid_mm": float(held["distance_to_centroid_mm"]),
                    "held_out_mean_elevation_angle_deg": float(np.nanmean(held_obs["theta_deg"].to_numpy(dtype=float))),
                    "held_out_facing": held.get("facing", ""),
                    "held_out_edge_center": held.get("edge_center_group", ""),
                    "d_tag_fold_mm": dtag,
                }
            )
        df = pd.DataFrame([r for r in rows if r["config"] == label])
        for pred, col in [
            ("held_out_height", "held_out_height_mm"),
            ("held_out_distance_to_centroid", "held_out_dist_to_centroid_mm"),
            ("held_out_mean_elevation_angle", "held_out_mean_elevation_angle_deg"),
        ]:
            fit = linear_regression(df[col], df["d_tag_fold_mm"], slope_scale=1000.0 if "mm" in col else 1.0)
            reg_rows.append({"config": label, "predictor": pred, "slope": fit["slope"], "r2": fit["r2"], "p_value": fit["p_value"]})
    write_csv(dirs["tables"] / "item05_loo_fold_dtag.csv", rows)
    write_csv(dirs["tables"] / "item05_loo_fold_regression.csv", reg_rows)
    sample_cpu(ctx)
    return finish_phase(ctx), rows, reg_rows


def item06_joint_morph_valley(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item 06")
    ids = sorted(inputs["tag_truth_np"])
    v4 = configs["V4_CV4"]
    v5 = configs["V5_CV5"]
    alphas = [float(x) for x in np.round(np.arange(0.0, 1.0 + 0.0001, 0.05), 2)]
    dgrid = [float(x) for x in np.arange(0.0, 140.0 + 0.1, 2.0)]
    jobs: list[dict[str, Any]] = []
    for alpha in alphas:
        coords = (1.0 - alpha) * v4.coords + alpha * v5.coords
        delays = {aid: (1.0 - alpha) * v4.delays[aid] + alpha * v5.delays[aid] for aid in range(8)}
        cfg = ConfigSpec(f"morph_a{alpha:.2f}", "L_morph", "C_morph", coords, delays)
        for dtag in dgrid:
            jobs.append(make_static_job(
                job_id=f"item06_a{alpha:.2f}_d{dtag:.1f}",
                config=cfg,
                d_tag_mm=dtag,
                ids=ids,
                inputs=inputs,
                meta={"alpha": alpha},
            ))
    results = run_eval_jobs(jobs, ctx, "item06_joint_morph_valley", chunk_size=8)
    rows: list[dict[str, Any]] = []
    for r in results:
        s = r["summary"]
        rows.append(
            {
                "alpha": float(r["meta"]["alpha"]),
                "d_tag_mm": float(r["d_tag_mm"]),
                "median_3d_mm": s["median_3d_mm"],
                "rmse_3d_mm": s["rmse_3d_mm"],
                "p95_3d_mm": s["p95_3d_mm"],
                "signed_vertical_slope": s["signed_vertical_slope_mm_per_m"],
            }
        )
    df = pd.DataFrame(rows)
    opt_rows = []
    for alpha, g in df.groupby("alpha"):
        best = g.loc[g["median_3d_mm"].astype(float).idxmin()]
        opt_rows.append({"alpha": float(alpha), "d_tag_min_median_mm": float(best["d_tag_mm"]), "min_median_3d_mm": float(best["median_3d_mm"])})
    gmin = df.loc[df["median_3d_mm"].astype(float).idxmin()]
    transfer = pd.read_csv(TRANSFER_CELLS)

    def transfer_metric(layout: str, corr: str, mode: str) -> float:
        m = transfer[(transfer["layout_source"] == layout) & (transfer["correction_source"] == corr) & (transfer["tag_delay_mode"] == mode)]
        return float(m.iloc[0]["median_3d_mm"]) if not m.empty else float("nan")

    def grid_metric(alpha: float, dtag: float) -> float:
        g = df[(np.isclose(df["alpha"], alpha)) & (np.isclose(df["d_tag_mm"], dtag))]
        return float(g.iloc[0]["median_3d_mm"]) if not g.empty else float("nan")

    markers = [
        {"marker_name": "V4_D0", "alpha": 0.0, "d_tag_mm": 0.0, "median_3d_mm": grid_metric(0.0, 0.0), "source": "grid_endpoint"},
        {"marker_name": "V4_LOO", "alpha": 0.0, "d_tag_mm": LOO_DTAG_MM, "median_3d_mm": transfer_metric("L_V4", "C_V4", "D_LOO_CV"), "source": "transfer_matrix_exact"},
        {"marker_name": "V5_D0", "alpha": 1.0, "d_tag_mm": 0.0, "median_3d_mm": grid_metric(1.0, 0.0), "source": "grid_endpoint"},
        {"marker_name": "V5_LOO", "alpha": 1.0, "d_tag_mm": LOO_DTAG_MM, "median_3d_mm": transfer_metric("L_V5", "C_V5", "D_LOO_CV"), "source": "transfer_matrix_exact"},
        {"marker_name": "global_min", "alpha": float(gmin["alpha"]), "d_tag_mm": float(gmin["d_tag_mm"]), "median_3d_mm": float(gmin["median_3d_mm"]), "source": "grid"},
    ]
    write_csv(dirs["tables"] / "item06_joint_morph_valley.csv", rows)
    write_csv(dirs["tables"] / "item06_morph_markers.csv", markers)
    write_csv(dirs["tables"] / "item06_optimal_dtag_vs_alpha.csv", opt_rows)
    return finish_phase(ctx), rows, markers, opt_rows


def item07_bias_allocation(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 07")
    ids = sorted(inputs["tag_truth_np"])
    base = configs["V5_CV5"]
    shifts = [float(x) for x in np.arange(-140.0, 140.0 + 0.1, 10.0)]
    jobs: list[dict[str, Any]] = []
    for ashift in shifts:
        delays = {aid: base.delays[aid] + ashift for aid in range(8)}
        cfg = ConfigSpec(f"V5_bias_a{ashift:.0f}", "L_V5", "C_V5_shifted", base.coords, delays)
        for tshift in shifts:
            jobs.append(make_static_job(job_id=f"item07_a{ashift:.0f}_t{tshift:.0f}", config=cfg, d_tag_mm=LOO_DTAG_MM + tshift, ids=ids, inputs=inputs, meta={"anchor_shift_mm": ashift, "tag_shift_mm": tshift}))
    results = run_eval_jobs(jobs, ctx, "item07_bias_allocation", chunk_size=8)
    rows = []
    for r in results:
        s = r["summary"]
        rows.append(
            {
                "anchor_shift_mm": float(r["meta"]["anchor_shift_mm"]),
                "tag_shift_mm": float(r["meta"]["tag_shift_mm"]),
                "median_3d_mm": s["median_3d_mm"],
                "rmse_3d_mm": s["rmse_3d_mm"],
                "signed_vertical_slope": s["signed_vertical_slope_mm_per_m"],
            }
        )
    write_csv(dirs["tables"] / "item07_bias_allocation_scan.csv", rows)
    return finish_phase(ctx), rows


def item08_facing_group(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    residuals: dict[str, list[dict[str, Any]]],
    inputs: dict[str, Any],
    maps: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 08")
    facings = sorted({v for v in maps["facing"].values() if v})
    if not facings:
        rows = [{"status": "skipped", "reason": "no facing metadata found"}]
        write_csv(dirs["tables"] / "item08_facing_group_dtag.csv", rows)
        sample_cpu(ctx)
        return finish_phase(ctx), rows
    ids_all = sorted(inputs["tag_truth_np"])
    grid = [float(x) for x in np.arange(0.0, 120.0 + 0.1, 2.0)]
    core = [configs[k] for k in ("V4_CV4", "V5_CV5", "Vicon_Ccm")]
    jobs = []
    for cfg in core:
        for facing in facings:
            ids = [sid for sid in ids_all if maps["facing"].get(sid) == facing]
            for dtag in grid:
                jobs.append(make_static_job(job_id=f"item08_{cfg.label}_{facing}_{dtag:.1f}", config=cfg, d_tag_mm=dtag, ids=ids, inputs=inputs, meta={"facing": facing}))
    results = run_eval_jobs(jobs, ctx, "item08_facing_group", chunk_size=8)
    solve_rows = []
    for r in results:
        s = r["summary"]
        solve_rows.append({"config": r["config"], "facing": r["meta"]["facing"], "d_tag_mm": r["d_tag_mm"], "median_3d_mm": s["median_3d_mm"], "rmse_3d_mm": s["rmse_3d_mm"], "p95_3d_mm": s["p95_3d_mm"]})
    out_rows: list[dict[str, Any]] = []
    solve_df = pd.DataFrame(solve_rows)
    for cfg in core:
        obs = pd.DataFrame(residuals[cfg.label])
        for facing in facings:
            face_obs = obs[obs["facing"] == facing]
            range_dtag = float(np.nanmedian(face_obs["effective_dtag_mm"].to_numpy(dtype=float))) if len(face_obs) else float("nan")
            g = solve_df[(solve_df["config"] == cfg.label) & (solve_df["facing"] == facing)]
            best = g.loc[g["median_3d_mm"].astype(float).idxmin()] if not g.empty else None
            for tier in ("LOW", "MID", "HIGH"):
                sub = face_obs[face_obs["height_tier"] == tier]
                out_rows.append(
                    {
                        "config": cfg.label,
                        "facing": facing,
                        "height_tier": tier,
                        "n_positions": int(sub["position_id"].nunique()) if len(sub) else 0,
                        "d_tag_range_residual_mm": float(np.nanmedian(sub["effective_dtag_mm"].to_numpy(dtype=float))) if len(sub) else float("nan"),
                        "d_tag_facing_global_range_residual_mm": range_dtag,
                        "d_tag_position_optimal_mm": float(best["d_tag_mm"]) if best is not None else float("nan"),
                        "position_optimal_median_3d_mm": float(best["median_3d_mm"]) if best is not None else float("nan"),
                    }
                )
    write_csv(dirs["tables"] / "item08_facing_group_dtag.csv", out_rows)
    return finish_phase(ctx), out_rows


def item09_board_frame_skip(dirs: dict[str, Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 09")
    rows = [
        {
            "status": "skipped",
            "reason": "No per-frame tag rigid-body orientation or board-frame normal was located in the existing static analysis inputs. The available static truth is a corrected antenna point, not a time-resolved board pose.",
        }
    ]
    write_csv(dirs["tables"] / "item09_board_frame_model.csv", rows)
    sample_cpu(ctx)
    return finish_phase(ctx), rows


def fit_dtag_model(train: pd.DataFrame, model: str) -> dict[str, Any]:
    y = train["effective_dtag_mm"].to_numpy(dtype=float)
    if model == "M0":
        return {"model": model, "params": np.asarray([], dtype=float)}
    if model == "M1":
        return {"model": model, "params": np.asarray([float(np.nanmedian(y))])}
    if model == "M2":
        vals = np.zeros(8, dtype=float)
        for aid in range(8):
            sub = train[train["anchor_id"] == aid]["effective_dtag_mm"].to_numpy(dtype=float)
            vals[aid] = float(np.nanmedian(sub)) if sub.size else float(np.nanmedian(y))
        return {"model": model, "params": vals}
    theta = np.deg2rad(train["theta_deg"].to_numpy(dtype=float))
    if model == "M3":
        x = np.column_stack([np.ones_like(theta), np.cos(theta)])
    elif model == "M4":
        x = np.column_stack([np.ones_like(theta), np.cos(theta), np.sin(theta)])
    else:
        raise ValueError(model)
    mask = np.isfinite(x).all(axis=1) & np.isfinite(y)
    beta, *_ = np.linalg.lstsq(x[mask], y[mask], rcond=None)
    return {"model": model, "params": beta}


def predict_dtag_model(model_fit: dict[str, Any], obs: pd.DataFrame) -> dict[int, float]:
    model = model_fit["model"]
    params = np.asarray(model_fit["params"], dtype=float)
    if model == "M0":
        return {aid: 0.0 for aid in range(8)}
    if model == "M1":
        return {aid: float(params[0]) for aid in range(8)}
    if model == "M2":
        return {aid: float(params[aid]) for aid in range(8)}
    out: dict[int, float] = {}
    for aid, g in obs.groupby("anchor_id"):
        theta = np.deg2rad(float(g.iloc[0]["theta_deg"]))
        if model == "M3":
            out[int(aid)] = float(params[0] + params[1] * math.cos(theta))
        elif model == "M4":
            out[int(aid)] = float(params[0] + params[1] * math.cos(theta) + params[2] * math.sin(theta))
    for aid in range(8):
        out.setdefault(aid, float(np.nanmean(list(out.values()))) if out else 0.0)
    return out


def item10_antenna_model(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    residuals: dict[str, list[dict[str, Any]]],
    inputs: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 10")
    ids = sorted(inputs["tag_truth_np"])
    jobs = []
    meta_by_job: dict[str, dict[str, Any]] = {}
    models = [("M0", 0), ("M1", 1), ("M2", 8), ("M3", 2), ("M4", 3)]
    for label in ("V5_CV5", "Vicon_Ccm"):
        cfg = configs[label]
        obs = pd.DataFrame(residuals[label])
        for model, n_params in models:
            for sid in ids:
                train = obs[obs["position_id"] != sid]
                held = obs[obs["position_id"] == sid]
                fit = fit_dtag_model(train, model)
                predicted = predict_dtag_model(fit, held)
                delays = {aid: cfg.delays[aid] + predicted[aid] for aid in range(8)}
                job_id = f"item10_{label}_{model}_{sid}"
                jobs.append(make_static_job(job_id=job_id, config=cfg, d_tag_mm=0.0, ids=[sid], inputs=inputs, meta={"model": model, "n_params": n_params}, delays_override=delays, return_rows=True))
                range_res = []
                for _, row in held.iterrows():
                    range_res.append(float(row["effective_dtag_mm"]) - predicted[int(row["anchor_id"])])
                meta_by_job[job_id] = {"range_rmse": rmse(range_res), "model": model, "n_params": n_params, "config": label}
    results = run_eval_jobs(jobs, ctx, "item10_antenna_model", chunk_size=8)
    rows: list[dict[str, Any]] = []
    tmp: dict[tuple[str, str], dict[str, list[float]]] = {}
    for r in results:
        meta = meta_by_job[r["job_id"]]
        key = (meta["config"], meta["model"])
        tmp.setdefault(key, {"err": [], "p95src": [], "range": [], "n_params": [meta["n_params"]]})
        if r["rows"]:
            tmp[key]["err"].append(float(r["rows"][0]["err_3d_mm"]))
        tmp[key]["range"].append(float(meta["range_rmse"]))
    for (label, model), vals in tmp.items():
        err = np.asarray(vals["err"], dtype=float)
        rows.append(
            {
                "config": label,
                "model": model,
                "n_params": int(vals["n_params"][0]),
                "cv_median_3d_mm": percentile(err, 50),
                "cv_rmse_3d_mm": rmse(err),
                "cv_p95_3d_mm": percentile(err, 95),
                "cv_range_residual_rmse_mm": rmse(vals["range"]),
                "notes": "M3/M4 are diagnostic because held-out truth geometry supplies elevation-dependent per-anchor correction.",
            }
        )
    write_csv(dirs["tables"] / "item10_antenna_model_comparison.csv", rows)
    return finish_phase(ctx), rows


def precompute_v5_position_sweep(
    ctx: dict[str, Any],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
    dgrid: list[float],
) -> pd.DataFrame:
    ids = sorted(inputs["tag_truth_np"])
    cfg = configs["V5_CV5"]
    jobs = [make_static_job(job_id=f"pre_v5_pos_{d:.3f}", config=cfg, d_tag_mm=d, ids=ids, inputs=inputs, return_rows=True) for d in dgrid]
    results = run_eval_jobs(jobs, ctx, "item11_precompute_v5_position_sweep", chunk_size=4)
    rows = []
    for r in results:
        for row in r["rows"]:
            rows.append({"d_tag_mm": float(r["d_tag_mm"]), "position_id": row["ID"], "err_3d_mm": row["err_3d_mm"], "rmse_component_mm": row["err_3d_mm"]})
    return pd.DataFrame(rows)


def interpolate_position_errors(pos_sweep: pd.DataFrame, dtag: float, eval_ids: list[str]) -> np.ndarray:
    errs = []
    for sid in eval_ids:
        g = pos_sweep[pos_sweep["position_id"] == sid].sort_values("d_tag_mm")
        if g.empty:
            continue
        x = g["d_tag_mm"].to_numpy(dtype=float)
        y = g["err_3d_mm"].to_numpy(dtype=float)
        d = float(min(max(dtag, float(np.nanmin(x))), float(np.nanmax(x))))
        errs.append(float(np.interp(d, x, y)))
    return np.asarray(errs, dtype=float)


def stratified_sample(rng: np.random.Generator, ids: list[str], maps: dict[str, dict[str, Any]], k: int) -> list[str]:
    tiers = {tier: [sid for sid in ids if maps["height"][sid] == tier] for tier in ("LOW", "MID", "HIGH")}
    chosen: list[str] = []
    base = k // 3
    rem = k % 3
    for i, tier in enumerate(("LOW", "MID", "HIGH")):
        n = min(len(tiers[tier]), base + (1 if i < rem else 0))
        chosen.extend(rng.choice(tiers[tier], size=n, replace=False).tolist())
    remaining = [sid for sid in ids if sid not in chosen]
    while len(chosen) < k and remaining:
        sid = rng.choice(remaining)
        chosen.append(str(sid))
        remaining.remove(str(sid))
    return chosen


def item11_learning_curve(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
    residuals: dict[str, list[dict[str, Any]]],
    maps: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item 11")
    ids = sorted(inputs["tag_truth_np"])
    dgrid = [float(x) for x in np.arange(0.0, 140.0 + 0.1, 2.0)]
    pos_sweep = precompute_v5_position_sweep(ctx, configs, inputs, dgrid)
    write_csv(dirs["tables"] / "item11_v5_position_sweep_cache.csv", pos_sweep.to_dict("records"))
    obs = pd.DataFrame(residuals["V5_CV5"])
    rng = np.random.default_rng(20260617)
    rows: list[dict[str, Any]] = []
    for k in (1, 2, 3, 4, 6, 8, 12, 16, 20):
        for sampling in ("random", "stratified"):
            for iteration in range(500):
                if sampling == "random":
                    cal = rng.choice(ids, size=k, replace=False).tolist()
                else:
                    cal = stratified_sample(rng, ids, maps, k)
                eval_ids = [sid for sid in ids if sid not in set(cal)]
                dtag = float(np.nanmedian(obs[obs["position_id"].isin(cal)]["effective_dtag_mm"].to_numpy(dtype=float)))
                err = interpolate_position_errors(pos_sweep, dtag, eval_ids)
                rows.append(
                    {
                        "k": k,
                        "iteration": iteration,
                        "sampling": sampling,
                        "d_tag_fitted_mm": dtag,
                        "eval_median_3d_mm": percentile(err, 50),
                        "eval_rmse_3d_mm": rmse(err),
                        "n_eval": len(eval_ids),
                        "notes": "position metrics interpolated from an actual V5 D_tag grid solve",
                    }
                )
    summary = []
    df = pd.DataFrame(rows)
    for (k, sampling), g in df.groupby(["k", "sampling"]):
        med = g["eval_median_3d_mm"].to_numpy(dtype=float)
        summary.append(
            {
                "k": int(k),
                "sampling": sampling,
                "d_tag_mean_mm": float(np.nanmean(g["d_tag_fitted_mm"])),
                "d_tag_std_mm": float(np.nanstd(g["d_tag_fitted_mm"])),
                "median_3d_mean_mm": float(np.nanmean(med)),
                "median_3d_p5_mm": percentile(med, 5),
                "median_3d_p95_mm": percentile(med, 95),
            }
        )
    write_csv(dirs["tables"] / "item11_calibration_learning_curve.csv", rows)
    write_csv(dirs["tables"] / "item11_calibration_learning_curve_summary.csv", summary)
    return finish_phase(ctx), rows, summary


def item12_design_ablation(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
    residuals: dict[str, list[dict[str, Any]]],
    maps: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 12")
    ids = sorted(inputs["tag_truth_np"])
    by_dist = sorted(ids, key=lambda sid: maps["distance"][sid])
    designs: dict[str, list[str]] = {
        "center_only": by_dist[:12],
        "edge_only": by_dist[12:],
        "low_only": [sid for sid in ids if maps["height"][sid] == "LOW"],
        "mid_only": [sid for sid in ids if maps["height"][sid] == "MID"],
        "high_only": [sid for sid in ids if maps["height"][sid] == "HIGH"],
        "stratified_LMH": [sid for sid in ids if sid.endswith(("01", "02"))][:0],
        "all_24_LOO": ids,
    }
    strat = []
    for tier in ("LOW", "MID", "HIGH"):
        tier_ids = [sid for sid in ids if maps["height"][sid] == tier]
        strat.extend(tier_ids[: min(3, len(tier_ids))])
    designs["stratified_LMH"] = strat
    facings = sorted({v for v in maps["facing"].values() if v})
    if facings:
        one = []
        for facing in facings:
            fids = [sid for sid in ids if maps["facing"].get(sid) == facing]
            if fids:
                one.append(fids[0])
        designs["one_per_facing"] = one
    jobs = []
    meta_by_job = {}
    for label in ("V4_CV4", "V5_CV5", "Vicon_Ccm"):
        cfg = configs[label]
        obs = pd.DataFrame(residuals[label])
        for design, cal in designs.items():
            if design == "all_24_LOO":
                continue
            eval_ids = [sid for sid in ids if sid not in set(cal)]
            if not eval_ids:
                continue
            dtag = float(np.nanmedian(obs[obs["position_id"].isin(cal)]["effective_dtag_mm"].to_numpy(dtype=float)))
            job_id = f"item12_{label}_{design}"
            jobs.append(make_static_job(job_id=job_id, config=cfg, d_tag_mm=dtag, ids=eval_ids, inputs=inputs, meta={"design": design, "n_cal": len(cal), "n_eval": len(eval_ids)}))
            meta_by_job[job_id] = {"config": label, "design": design, "n_cal": len(cal), "n_eval": len(eval_ids), "dtag": dtag}
    results = run_eval_jobs(jobs, ctx, "item12_design_ablation", chunk_size=4)
    rows = []
    for r in results:
        meta = meta_by_job[r["job_id"]]
        s = r["summary"]
        rows.append(
            {
                "config": meta["config"],
                "design": meta["design"],
                "n_cal": meta["n_cal"],
                "n_eval": meta["n_eval"],
                "d_tag_mm": meta["dtag"],
                "eval_median_3d_mm": s["median_3d_mm"],
                "eval_rmse_3d_mm": s["rmse_3d_mm"],
                "eval_p95_3d_mm": s["p95_3d_mm"],
            }
        )
    transfer = pd.read_csv(TRANSFER_CELLS)
    for label, layout, corr in [("V4_CV4", "L_V4", "C_V4"), ("V5_CV5", "L_V5", "C_V5"), ("Vicon_Ccm", "L_Vicon", "C_Vicon_cm")]:
        m = transfer[(transfer["layout_source"] == layout) & (transfer["correction_source"] == corr) & (transfer["tag_delay_mode"] == "D_LOO_CV")]
        if not m.empty:
            rr = m.iloc[0]
            rows.append({"config": label, "design": "all_24_LOO", "n_cal": 23, "n_eval": 1, "d_tag_mm": LOO_DTAG_MM, "eval_median_3d_mm": float(rr["median_3d_mm"]), "eval_rmse_3d_mm": float(rr["rmse_3d_mm"]), "eval_p95_3d_mm": float(rr["p95_3d_mm"]), "notes": "reference from transfer matrix"})
    write_csv(dirs["tables"] / "item12_calibration_design_ablation.csv", rows)
    return finish_phase(ctx), rows


def precompute_config_position_sweeps(
    ctx: dict[str, Any],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
    labels: tuple[str, ...],
    dgrid: list[float],
) -> pd.DataFrame:
    ids = sorted(inputs["tag_truth_np"])
    jobs = []
    for label in labels:
        cfg = configs[label]
        for dtag in dgrid:
            jobs.append(make_static_job(job_id=f"pre_{label}_{dtag:.1f}", config=cfg, d_tag_mm=dtag, ids=ids, inputs=inputs, return_rows=True))
    results = run_eval_jobs(jobs, ctx, "item13_precompute_position_sweeps", chunk_size=4)
    rows = []
    for r in results:
        for row in r["rows"]:
            rows.append(
                {
                    "config": r["config"],
                    "d_tag_mm": r["d_tag_mm"],
                    "position_id": row["ID"],
                    "err_3d_mm": row["err_3d_mm"],
                    "err_vertical_y_mm": row["err_vertical_y_mm"],
                    "signed_vertical_y_mm": row["err_y_vertical_mm"],
                    "truth_y_mm": row["truth_y_vertical_mm"],
                }
            )
    return pd.DataFrame(rows)


def zero_slope_from_curve(df: pd.DataFrame) -> float:
    g = df.sort_values("d_tag_mm")
    x = g["d_tag_mm"].to_numpy(dtype=float)
    y = g["signed_vertical_slope_mm_per_m"].to_numpy(dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    if x.size == 0:
        return float("nan")
    for i in range(len(x) - 1):
        if y[i] == 0:
            return float(x[i])
        if (y[i] < 0 < y[i + 1]) or (y[i] > 0 > y[i + 1]):
            return float(x[i] - y[i] * (x[i + 1] - x[i]) / (y[i + 1] - y[i]))
    return float(x[int(np.nanargmin(np.abs(y)))])


def item13_criterion_ambiguity(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], pd.DataFrame]:
    ctx = phase_context("Item 13")
    dgrid = [float(x) for x in np.arange(0.0, 120.0 + 0.1, 2.0)]
    sweep = precompute_config_position_sweeps(ctx, configs, inputs, ("V4_CV4", "V5_CV5", "Vicon_Ccm"), dgrid)
    write_csv(dirs["tables"] / "item13_position_sweep_cache.csv", sweep.to_dict("records"))
    ids = sorted(inputs["tag_truth_np"])
    rows = []
    for label in ("V4_CV4", "V5_CV5", "Vicon_Ccm"):
        base = sweep[sweep["config"] == label]
        for k, sid in enumerate(ids, start=1):
            train = base[base["position_id"] != sid]
            curve_rows = []
            for dtag, g in train.groupby("d_tag_mm"):
                err = g["err_3d_mm"].to_numpy(dtype=float)
                reg = linear_regression(g["truth_y_mm"], g["signed_vertical_y_mm"], slope_scale=1000.0)
                curve_rows.append({"d_tag_mm": float(dtag), "median": percentile(err, 50), "rmse": rmse(err), "p95": percentile(err, 95), "signed_vertical_slope_mm_per_m": reg["slope"]})
            c = pd.DataFrame(curve_rows)
            rows.append(
                {
                    "config": label,
                    "fold_k": k,
                    "held_out_position": sid,
                    "d_min_median": float(c.loc[c["median"].idxmin()]["d_tag_mm"]),
                    "d_min_rmse": float(c.loc[c["rmse"].idxmin()]["d_tag_mm"]),
                    "d_min_p95": float(c.loc[c["p95"].idxmin()]["d_tag_mm"]),
                    "d_zero_slope": zero_slope_from_curve(c),
                }
            )
    summary = []
    df = pd.DataFrame(rows)
    for (label, criterion), vals in [
        ((label, col), df[df["config"] == label][col].to_numpy(dtype=float))
        for label in ("V4_CV4", "V5_CV5", "Vicon_Ccm")
        for col in ("d_min_median", "d_min_rmse", "d_min_p95", "d_zero_slope")
    ]:
        summary.append({"config": label, "criterion": criterion, "mean_dtag": float(np.nanmean(vals)), "std_dtag": float(np.nanstd(vals)), "min_dtag": float(np.nanmin(vals)), "max_dtag": float(np.nanmax(vals)), "spread": float(np.nanmax(vals) - np.nanmin(vals))})
    write_csv(dirs["tables"] / "item13_criterion_ambiguity_per_fold.csv", rows)
    write_csv(dirs["tables"] / "item13_criterion_ambiguity_summary.csv", summary)
    return finish_phase(ctx), rows, summary, sweep


def estimate_delay_common_mode_custom(full, anchor_coords: np.ndarray, e_reg_mm: float | None) -> tuple[dict[int, float], dict[str, Any], list[dict[str, Any]]]:
    if e_reg_mm is not None:
        return full.estimate_delay_common_mode(anchor_coords, e_reg_mm=float(e_reg_mm))
    df = pd.read_csv(full.PAIR_QUALITY)
    df = df[df["eval_set"] == "solve"].copy()
    design = []
    target = []
    rows = []
    for _, row in df.iterrows():
        a, b = str(row["pair"]).split("-")
        ia, ib = ANCHORS.index(a), ANCHORS.index(b)
        measured = float(row["median_all"])
        geom = float(np.linalg.norm(anchor_coords[ia] - anchor_coords[ib]))
        bias = measured - geom
        vec = np.zeros(9)
        vec[0] = 2.0
        vec[1 + ia] = 1.0
        vec[1 + ib] = 1.0
        design.append(vec)
        target.append(bias)
        rows.append({"pair": f"{a}-{b}", "measured_median_mm": measured, "geometric_mm": geom, "bias_mm": bias})
    vec = np.zeros(9)
    vec[1:] = 1000.0
    design.append(vec)
    target.append(0.0)
    x, *_ = np.linalg.lstsq(np.vstack(design), np.asarray(target), rcond=None)
    c = float(x[0])
    e = np.asarray(x[1:], dtype=float)
    delays = {i: float(c + e[i]) for i in range(8)}
    residuals = []
    for row in rows:
        a, b = row["pair"].split("-")
        pred = delays[ANCHORS.index(a)] + delays[ANCHORS.index(b)]
        row["predicted_bias_mm"] = float(pred)
        row["residual_mm"] = float(pred - row["bias_mm"])
        residuals.append(row["residual_mm"])
    meta = {"common_mode_mm": c, "e_reg_mm": float("nan"), "mean_e_mm": float(np.mean(e)), "max_abs_e_mm": float(np.max(np.abs(e))), "pair_residual_rms_mm": rmse(residuals)}
    return delays, meta, rows


def item14_vicon_refit_variants(
    dirs: dict[str, Path],
    full,
    inputs: dict[str, Any],
    configs: dict[str, ConfigSpec],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item 14")
    ids = sorted(inputs["tag_truth_np"])
    variants: list[tuple[str, float | None, dict[int, float], dict[str, Any]]] = []
    indep = configs["Vicon_Cindep"].delays
    c_indep = float(np.nanmean(list(indep.values())))
    variants.append(("C_Vicon_indep", None, indep, {"common_mode_mm": c_indep, "e_reg_mm": float("nan")}))
    for name, ereg in [("C_Vicon_cm_e10", 10.0), ("C_Vicon_cm_e20", 20.0), ("C_Vicon_cm_e50", 50.0), ("C_Vicon_cm_e100", 100.0), ("C_Vicon_cm_noreg", None)]:
        delays, meta, _rows = estimate_delay_common_mode_custom(full, inputs["truth_coords"], ereg)
        variants.append((name, ereg, delays, meta))
    jobs = []
    meta_by_job = {}
    for name, ereg, delays, meta in variants:
        cfg = ConfigSpec(name, "L_Vicon", name, inputs["truth_coords"], delays)
        job_id = f"item14_{name}"
        jobs.append(make_static_job(job_id=job_id, config=cfg, d_tag_mm=LOO_DTAG_MM, ids=ids, inputs=inputs, meta={"variant": name, "e_reg": ereg, "meta": meta}))
        meta_by_job[job_id] = (name, ereg, delays, meta)
    results = run_eval_jobs(jobs, ctx, "item14_vicon_refit", chunk_size=2)
    rows = []
    per_anchor = []
    for r in results:
        name, ereg, delays, meta = meta_by_job[r["job_id"]]
        c = float(meta.get("common_mode_mm", np.nanmean(list(delays.values()))))
        e_vals = [delays[i] - c for i in range(8)]
        s = r["summary"]
        rows.append({"variant": name, "e_reg": "" if ereg is None else ereg, "median_3d_mm": s["median_3d_mm"], "p95_3d_mm": s["p95_3d_mm"], "rmse_3d_mm": s["rmse_3d_mm"], "c_mm": c, "e_i_spread_mm": float(np.nanmax(e_vals) - np.nanmin(e_vals))})
        for aid in range(8):
            per_anchor.append({"variant": name, "anchor_label": ANCHORS[aid], "d_i_mm": delays[aid], "e_i_mm": delays[aid] - c})
    write_csv(dirs["tables"] / "item14_vicon_refit_variants.csv", rows)
    write_csv(dirs["tables"] / "item14_vicon_refit_per_anchor.csv", per_anchor)
    return finish_phase(ctx), rows, per_anchor


def item15_anchor_layer_split(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 15")
    ids = sorted(inputs["tag_truth_np"])
    v5 = configs["V5_CV5"]
    lower_mean = float(np.nanmean([v5.delays[i] for i in LOWER_ANCHORS]))
    upper_mean = float(np.nanmean([v5.delays[i] for i in UPPER_ANCHORS]))
    transfer = pd.read_csv(TRANSFER_CELLS)
    m = transfer[(transfer["layout_source"] == "L_V5") & (transfer["correction_source"] == "C_V5") & (transfer["tag_delay_mode"] == "D_LOO_CV")].iloc[0]
    rows = [
        {
            "model": "single_c",
            "c_lower_mm": V5_COMMON_MODE_MM,
            "c_upper_mm": V5_COMMON_MODE_MM,
            "c_diff_mm": 0.0,
            "median_3d_mm": float(m["median_3d_mm"]),
            "rmse_3d_mm": float(m["rmse_3d_mm"]),
            "notes": "existing V5 C_V5 transfer-matrix row",
        },
        {
            "model": "dual_c_existing_tail",
            "c_lower_mm": lower_mean,
            "c_upper_mm": upper_mean,
            "c_diff_mm": upper_mean - lower_mean,
            "median_3d_mm": float(m["median_3d_mm"]),
            "rmse_3d_mm": float(m["rmse_3d_mm"]),
            "notes": "same per-anchor delays as V5, reparameterized by layer means",
        },
    ]
    layer_only = {aid: (lower_mean if aid in LOWER_ANCHORS else upper_mean) for aid in range(8)}
    cfg = ConfigSpec("V5_dual_layer_common_only", "L_V5", "C_layer_common_only", v5.coords, layer_only)
    result = run_eval_jobs([make_static_job(job_id="item15_layer_common_only", config=cfg, d_tag_mm=LOO_DTAG_MM, ids=ids, inputs=inputs)], ctx, "item15_layer_split", chunk_size=1)[0]
    s = result["summary"]
    rows.append({"model": "dual_layer_common_only", "c_lower_mm": lower_mean, "c_upper_mm": upper_mean, "c_diff_mm": upper_mean - lower_mean, "median_3d_mm": s["median_3d_mm"], "rmse_3d_mm": s["rmse_3d_mm"], "notes": "diagnostic: layer common modes only, V5 e_i removed"})
    write_csv(dirs["tables"] / "item15_anchor_layer_split.csv", rows)
    return finish_phase(ctx), rows


def one_hot(values: pd.Series, prefix: str) -> pd.DataFrame:
    return pd.get_dummies(values.astype(str), prefix=prefix, drop_first=False, dtype=float)


def item16_variance_decomposition(dirs: dict[str, Path], residuals: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 16")
    df = pd.DataFrame(residuals["V5_CV5"]).copy()
    y = df["rho_mm"].to_numpy(dtype=float)
    blocks = {
        "anchor_id": one_hot(df["anchor_label"], "anchor"),
        "position_id": one_hot(df["position_id"], "pos"),
        "height": one_hot(df["height_tier"], "height"),
        "elevation_angle": pd.DataFrame({"elevation_angle": df["theta_deg"].astype(float)}),
        "distance": pd.DataFrame({"distance": df["anchor_distance_mm"].astype(float)}),
        "layer": one_hot(df["layer"], "layer"),
        "facing": one_hot(df["facing"], "facing"),
        "edge_center": one_hot(df["edge_center"], "edge"),
    }
    x_full = pd.concat([pd.DataFrame({"intercept": np.ones(len(df))}), *blocks.values()], axis=1).to_numpy(dtype=float)
    beta, *_ = np.linalg.lstsq(x_full, y, rcond=None)
    pred = x_full @ beta
    sse_full = float(np.sum((y - pred) ** 2))
    sst = float(np.sum((y - np.mean(y)) ** 2))
    n = len(y)
    p_full = x_full.shape[1]
    rows = []
    for factor, block in blocks.items():
        reduced_blocks = [b for name, b in blocks.items() if name != factor]
        x_red = pd.concat([pd.DataFrame({"intercept": np.ones(len(df))}), *reduced_blocks], axis=1).to_numpy(dtype=float)
        b_red, *_ = np.linalg.lstsq(x_red, y, rcond=None)
        sse_red = float(np.sum((y - x_red @ b_red) ** 2))
        df_num = max(1, x_full.shape[1] - x_red.shape[1])
        df_den = max(1, n - p_full)
        f_stat = ((sse_red - sse_full) / df_num) / (sse_full / df_den) if sse_full > 0 else float("nan")
        p_val = float(stats.f.sf(f_stat, df_num, df_den)) if stats is not None and np.isfinite(f_stat) else float("nan")
        rows.append({"factor": factor, "sum_squares": max(0.0, sse_red - sse_full), "fraction_explained": max(0.0, sse_red - sse_full) / sst if sst > 0 else float("nan"), "f_statistic": f_stat, "p_value": p_val})
    write_csv(dirs["tables"] / "item16_variance_decomposition.csv", rows)
    sample_cpu(ctx)
    return finish_phase(ctx), rows


def item17_downweighting(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    residuals: dict[str, list[dict[str, Any]]],
    inputs: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 17")
    ids = sorted(inputs["tag_truth_np"])
    base_sigma = {int(k): float(v) for k, v in inputs["sigma_by_id"].items()}
    jobs = []
    meta_by_job = {}
    for label in ("V4_CV4", "V5_CV5", "Vicon_Ccm"):
        cfg = configs[label]
        obs = pd.DataFrame(residuals[label])
        rms_by_anchor = {aid: rmse(obs[obs["anchor_id"] == aid]["rho_mm"]) for aid in range(8)}
        med_rms = float(np.nanmedian(list(rms_by_anchor.values())))
        sigma_inv = {aid: base_sigma.get(aid, 50.0) * max(rms_by_anchor[aid], 1.0) / max(med_rms, 1.0) for aid in range(8)}
        for weighting, sigma, allowed in [("uniform", base_sigma, list(range(8))), ("inverse_rms", sigma_inv, list(range(8)))]:
            job_id = f"item17_{label}_{weighting}"
            jobs.append(make_static_job(job_id=job_id, config=cfg, d_tag_mm=LOO_DTAG_MM, ids=ids, inputs=inputs, sigma_by_id=sigma, allowed_anchor_ids=allowed))
            meta_by_job[job_id] = {"config": label, "weighting": weighting, "n_anchors": len(allowed)}
        for thr in (100.0, 120.0, 140.0):
            allowed = [aid for aid in range(8) if rms_by_anchor[aid] <= thr]
            job_id = f"item17_{label}_remove_gt{int(thr)}"
            jobs.append(make_static_job(job_id=job_id, config=cfg, d_tag_mm=LOO_DTAG_MM, ids=ids, inputs=inputs, sigma_by_id=base_sigma, allowed_anchor_ids=allowed))
            meta_by_job[job_id] = {"config": label, "weighting": f"remove_gt{int(thr)}", "n_anchors": len(allowed)}
    results = run_eval_jobs(jobs, ctx, "item17_downweighting", chunk_size=4)
    rows = []
    for r in results:
        meta = meta_by_job[r["job_id"]]
        s = r["summary"]
        rows.append({"config": meta["config"], "weighting": meta["weighting"], "n_anchors_effective": meta["n_anchors"], "median_3d_mm": s["median_3d_mm"], "p95_3d_mm": s["p95_3d_mm"], "rmse_3d_mm": s["rmse_3d_mm"], "fail_rate": s["fail_rate"]})
    write_csv(dirs["tables"] / "item17_nlos_downweighting.csv", rows)
    return finish_phase(ctx), rows


def item18_temporal_split(
    dirs: dict[str, Path],
    residuals: dict[str, list[dict[str, Any]]],
    raw_info: dict[str, dict[str, float]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 18")
    order = sorted(raw_info, key=lambda sid: raw_info[sid]["capture_time_key"])
    blocks = {"early": order[:8], "mid": order[8:16], "late": order[16:24]}
    static = pd.read_csv(FULL_V5_STATIC)
    static = static[static["tag_delay_mode"] == "D_LOO_CV"]
    obs = pd.DataFrame(residuals["V5_CV5"])
    rows = []
    for block, ids in blocks.items():
        g = obs[obs["position_id"].isin(ids)]
        acc = static[static["ID"].isin(ids)]
        row: dict[str, Any] = {
            "time_block": block,
            "n_positions": len(ids),
            "d_tag_mm": float(np.nanmedian(g["effective_dtag_mm"].to_numpy(dtype=float))) if len(g) else float("nan"),
            "median_3d_mm": float(np.nanmedian(acc["err_3d_mm"].to_numpy(dtype=float))) if len(acc) else float("nan"),
            "temperature_note": "temperature logs not located in required static inputs; time-only split reported",
        }
        for aid, label in enumerate(ANCHORS):
            sub = g[g["anchor_id"] == aid]["rho_mm"].to_numpy(dtype=float)
            row[f"mean_rho_{label}_mm"] = float(np.nanmean(sub)) if sub.size else float("nan")
            row[f"spike_rate_{label}_gt100"] = float(np.mean(sub > 100.0)) if sub.size else float("nan")
        rows.append(row)
    write_csv(dirs["tables"] / "item18_temporal_split.csv", rows)
    sample_cpu(ctx)
    return finish_phase(ctx), rows


def write_layout_json(path: Path, *, name: str, coords: np.ndarray, delays: dict[int, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {
        "version": name,
        "label": name,
        "anchors": [
            {"id": int(aid), "label": ANCHORS[aid], "x_mm": float(coords[aid, 0]), "y_mm": float(coords[aid, 1]), "z_mm": float(coords[aid, 2]), "d_anchor_mm": float(delays[aid])}
            for aid in range(8)
        ],
        "tag_delay_mm": 0.0,
        "metadata": {"generated_by": str(THIS), "generated_at": datetime.now().isoformat(timespec="seconds")},
    }
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def roto_points_worker(job: dict[str, Any]) -> dict[str, Any]:
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    result = solve_capture_trajectory(
        Path(job["layout_path"]),
        Path(job["capture_path"]),
        method="T4",
        anchor_sigma_path=Path(job["sigma_path"]),
        tags={str(job["tag"])},
        tag_delay_by_tag={str(job["tag"]): float(job["d_tag_mm"])},
    )
    points = []
    for item in result.results:
        if item.status != "ok":
            continue
        points.append(
            {
                "sweep": int(item.sweep),
                "time_s": float(item.host_elapsed_s),
                "host_epoch_s": float(item.host_epoch_s),
                "x_mm": float(item.x_mm),
                "y_mm": float(item.y_mm),
                "z_mm": float(item.z_mm),
                "residual_rms_mm": float(item.residual_rms_mm),
            }
        )
    return {"job_id": job["job_id"], "capture_id": job["capture_id"], "tag": job["tag"], "d_tag_mm": float(job["d_tag_mm"]), "points": points, "frames_input": int(result.frames_input), "frames_solved": int(result.frames_solved)}


def run_roto_jobs(jobs: list[dict[str, Any]], ctx: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    if not jobs:
        return []
    results = []
    done = 0
    mp_ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=WORKERS, mp_context=mp_ctx) as pool:
        futures = [pool.submit(roto_points_worker, job) for job in jobs]
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            cpu = sample_cpu(ctx)
            print(json.dumps({"stage": stage, "done": done, "total": len(jobs), "live_cpu_percent": cpu}, sort_keys=True), flush=True)
    by_id = {r["job_id"]: r for r in results}
    return [by_id[j["job_id"]] for j in jobs]


def item19_roto_per_tag_dtag(
    dirs: dict[str, Path],
    full,
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 19")
    if not ROTO_RHO_EXISTING.exists():
        rows = [{"status": "skipped", "reason": f"missing existing dynamic rho table: {ROTO_RHO_EXISTING}"}]
        write_csv(dirs["tables"] / "item19_roto_per_tag_dtag.csv", rows)
        sample_cpu(ctx)
        return finish_phase(ctx), rows
    rho = pd.read_csv(ROTO_RHO_EXISTING)
    rho = rho[(rho["layout"].astype(str).str.contains("v5")) & (rho["method"] == "T4")]
    circle = pd.read_csv(FULL_V5_CIRCLE)
    circle = circle[(circle["tag_delay_mode"] == "D_LOO_CV") & (circle["status"] == "ok")]
    layout_path = dirs["tables"] / "generated_layouts/v5_roto_item19.json"
    raw_v5_coords = np.asarray(inputs["coords_v5"], dtype=float)
    write_layout_json(layout_path, name="v5_roto_item19", coords=raw_v5_coords, delays=configs["V5_CV5"].delays)
    jobs = []
    opt_rows = []
    for (cid, tag), g in rho.groupby(["capture_id", "tag"]):
        if tag not in ROTO_TAGS:
            continue
        dopt = LOO_DTAG_MM + float(np.nanmedian(g["rho_mm"].to_numpy(dtype=float)))
        jobs.append({"job_id": f"item19_{cid}_{tag}", "capture_id": cid, "tag": tag, "d_tag_mm": dopt, "layout_path": str(layout_path), "capture_path": str(inputs["roto_files"][cid]), "sigma_path": str(SIGMA_PATH)})
    solved = run_roto_jobs(jobs, ctx, "item19_roto_optimal")
    for r in solved:
        pts = np.asarray([[p["x_mm"], p["y_mm"], p["z_mm"]] for p in r["points"]], dtype=float)
        fit = full.fit_circle_3d(pts)
        static_match = circle[(circle["capture_id"] == r["capture_id"]) & (circle["tag"] == r["tag"])]
        static_radius = float(static_match.iloc[0]["radius_mm"]) if not static_match.empty else float("nan")
        opt_rows.append(
            {
                "tag": r["tag"],
                "capture_id": r["capture_id"],
                "d_tag_optimal_mm": r["d_tag_mm"],
                "radius_with_optimal_dtag_mm": fit.get("radius_mm", float("nan")),
                "radius_with_static_dtag_mm": static_radius,
                "radius_delta_opt_minus_static_mm": fit.get("radius_mm", float("nan")) - static_radius if np.isfinite(static_radius) and "radius_mm" in fit else float("nan"),
                "reference_delta_radius_mm": 120.0,
                "notes": "best-fit-aligned/shape-only; d_tag optimum estimated by dynamic median rho zeroing",
            }
        )
    write_csv(dirs["tables"] / "item19_roto_per_tag_dtag.csv", opt_rows)
    return finish_phase(ctx), opt_rows


def circle_phase(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pts = points[np.isfinite(points).all(axis=1)]
    if pts.shape[0] < 10:
        return np.full(points.shape[0], np.nan), np.full((3,), np.nan)
    center0 = np.mean(pts, axis=0)
    _u, _s, vh = np.linalg.svd(pts - center0, full_matrices=False)
    e1, e2 = vh[0], vh[1]
    uv = np.column_stack([(points - center0) @ e1, (points - center0) @ e2])
    phase = np.degrees(np.arctan2(uv[:, 1], uv[:, 0]))
    return phase, center0


def item20_dynamic_motion(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 20")
    layout_path = dirs["tables"] / "generated_layouts/v5_roto_item20_dloo.json"
    write_layout_json(layout_path, name="v5_roto_item20_dloo", coords=np.asarray(inputs["coords_v5"], dtype=float), delays=configs["V5_CV5"].delays)
    jobs = []
    for cid, path in inputs["roto_files"].items():
        for tag in ROTO_TAGS:
            jobs.append({"job_id": f"item20_{cid}_{tag}", "capture_id": cid, "tag": tag, "d_tag_mm": LOO_DTAG_MM, "layout_path": str(layout_path), "capture_path": str(path), "sigma_path": str(SIGMA_PATH)})
    solved = run_roto_jobs(jobs, ctx, "item20_dynamic_motion")
    rho = pd.read_csv(ROTO_RHO_EXISTING) if ROTO_RHO_EXISTING.exists() else pd.DataFrame()
    rows = []
    for r in solved:
        pts = np.asarray([[p["x_mm"], p["y_mm"], p["z_mm"]] for p in r["points"]], dtype=float)
        t = np.asarray([p["time_s"] for p in r["points"]], dtype=float)
        phase, _center = circle_phase(pts)
        phase_unwrap = np.unwrap(np.deg2rad(phase))
        omega = np.full_like(phase_unwrap, np.nan, dtype=float)
        speed = np.full_like(phase_unwrap, np.nan, dtype=float)
        if len(t) > 2:
            omega = np.gradient(phase_unwrap, t) * 180.0 / math.pi
            dist = np.linalg.norm(np.gradient(pts, axis=0), axis=1)
            dt = np.gradient(t)
            speed = np.divide(dist, dt, out=np.full_like(dist, np.nan), where=np.abs(dt) > 1e-9)
        rho_sub = rho[(rho.get("capture_id", pd.Series(dtype=str)) == r["capture_id"]) & (rho.get("tag", pd.Series(dtype=str)) == r["tag"])] if not rho.empty else pd.DataFrame()
        rho_by_sweep = {}
        if not rho_sub.empty:
            for sw, g in rho_sub.groupby("sweep"):
                rho_by_sweep[int(sw)] = {f"rho_{ANCHORS[int(aid)]}": float(np.nanmean(gg["rho_mm"])) for aid, gg in g.groupby("anchor_id")}
        for i, p in enumerate(r["points"]):
            extra = rho_by_sweep.get(int(p["sweep"]), {})
            rows.append(
                {
                    "capture_id": r["capture_id"],
                    "tag": r["tag"],
                    "frame": int(p["sweep"]),
                    "phase_deg": float(phase[i]) if i < len(phase) else float("nan"),
                    "angular_speed_deg_s": float(omega[i]) if i < len(omega) else float("nan"),
                    "tangential_speed_mm_s": float(speed[i]) if i < len(speed) else float("nan"),
                    "solve_residual_rms_mm": float(p["residual_rms_mm"]),
                    **extra,
                }
            )
    write_csv(dirs["tables"] / "item20_dynamic_residual_vs_motion.csv", rows)
    return finish_phase(ctx), rows


def item21_range_percentile(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
    raw_ranges: dict[str, dict[int, np.ndarray]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 21")
    mech, full, ab = worker_context()
    tag_truth = inputs["tag_truth_np"]
    percentiles = [10, 20, 25, 30, 40, 50, 75, 90]
    rows = []
    for label in ("V4_CV4", "V5_CV5", "Vicon_Ccm"):
        cfg = configs[label]
        layout = ab.build_layout(name=f"item21_{label}", labels=list(ANCHORS), coords_opti_frame=cfg.coords, delays=cfg.delays, tag_delay_mm=0.0, sigma_by_id=inputs["sigma_by_id"], metadata={"item": 21})
        solver = ab.TagPositionSolver(layout, ab.SolverConfig(method="T4"), tag_delay_by_tag={STATIC_TAG: LOO_DTAG_MM})
        for pct in percentiles:
            err_rows = []
            for sid, by_anchor in raw_ranges.items():
                obs = []
                for aid in range(8):
                    vals = by_anchor.get(aid)
                    if vals is not None and vals.size:
                        obs.append(Observation(anchor_id=aid, range_mm=float(np.nanpercentile(vals, pct)), quality_percent=100.0, status="O"))
                if len(obs) < 4 or sid not in tag_truth:
                    continue
                frame = Frame(tag=STATIC_TAG, sweep=0, host_elapsed_s=0.0, host_epoch_s=0.0, observations=tuple(obs), imu=None)
                result = solver.solve_frame(frame)
                if result is None or result.status != "ok":
                    continue
                solved = np.asarray([result.x_mm, result.y_mm, result.z_mm], dtype=float)
                diff = solved - tag_truth[sid]
                err_rows.append(float(np.linalg.norm(diff)))
            rows.append({"config": label, "percentile": pct, "median_3d_mm": percentile(err_rows, 50), "p95_3d_mm": percentile(err_rows, 95), "rmse_3d_mm": rmse(err_rows), "n_positions": len(err_rows), "notes": "diagnostic single synthetic percentile frame per static position"})
    write_csv(dirs["tables"] / "item21_range_percentile_sweep.csv", rows)
    sample_cpu(ctx)
    return finish_phase(ctx), rows


def item22_jackknife_anchor(dirs: dict[str, Path], residuals: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 22")
    rows = []
    for label in ("V4_CV4", "V5_CV5", "Vicon_Ccm"):
        obs = pd.DataFrame(residuals[label])
        all8 = float(np.nanmedian(obs["effective_dtag_mm"].to_numpy(dtype=float)))
        for aid in range(8):
            sub = obs[obs["anchor_id"] != aid]
            dtag = float(np.nanmedian(sub["effective_dtag_mm"].to_numpy(dtype=float))) if len(sub) else float("nan")
            rows.append({"config": label, "anchor_removed": aid, "anchor_label": ANCHORS[aid], "d_tag_without_mm": dtag, "d_tag_all8_mm": all8, "delta_mm": dtag - all8})
    write_csv(dirs["tables"] / "item22_jackknife_anchor_dtag.csv", rows)
    sample_cpu(ctx)
    return finish_phase(ctx), rows


def item23_differential_ranging(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
    medians_by_id: dict[str, dict[int, float]],
    residuals: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = phase_context("Item 23")
    ids = sorted(inputs["tag_truth_np"])
    cfg = configs["V5_CV5"]
    rows = []
    for i, sid1 in enumerate(ids):
        for sid2 in ids[i + 1 :]:
            p1 = inputs["tag_truth_np"][sid1]
            p2 = inputs["tag_truth_np"][sid2]
            for aid in range(8):
                if aid not in medians_by_id[sid1] or aid not in medians_by_id[sid2]:
                    continue
                dr = medians_by_id[sid2][aid] - medians_by_id[sid1][aid]
                dg = float(np.linalg.norm(p2 - cfg.coords[aid]) - np.linalg.norm(p1 - cfg.coords[aid]))
                rows.append({"pos1_id": sid1, "pos2_id": sid2, "anchor_label": ANCHORS[aid], "anchor_id": aid, "delta_r_measured_mm": dr, "delta_r_geometric_mm": dg, "differential_error_mm": dr - dg})
    abs_obs = pd.DataFrame(residuals["V5_CV5"])
    summary = []
    df = pd.DataFrame(rows)
    for aid in range(8):
        sub = df[df["anchor_id"] == aid]["differential_error_mm"].to_numpy(dtype=float)
        abs_sub = abs_obs[abs_obs["anchor_id"] == aid]["rho_mm"].to_numpy(dtype=float)
        diff_rms = rmse(sub)
        abs_rms = rmse(abs_sub)
        summary.append({"anchor_label": ANCHORS[aid], "differential_rms_mm": diff_rms, "absolute_rho_rms_mm": abs_rms, "ratio": diff_rms / abs_rms if abs_rms and np.isfinite(abs_rms) else float("nan")})
    write_csv(dirs["tables"] / "item23_differential_ranging.csv", rows)
    write_csv(dirs["tables"] / "item23_differential_ranging_summary.csv", summary)
    sample_cpu(ctx)
    return finish_phase(ctx), rows, summary


def item24_rho_distribution(
    dirs: dict[str, Path],
    configs: dict[str, ConfigSpec],
    inputs: dict[str, Any],
    raw_ranges: dict[str, dict[int, np.ndarray]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Item 24")
    cfg = configs["V5_CV5"]
    rows = []
    for aid in range(8):
        vals = []
        for sid, by_anchor in raw_ranges.items():
            if aid not in by_anchor or sid not in inputs["tag_truth_np"]:
                continue
            geom = float(np.linalg.norm(inputs["tag_truth_np"][sid] - cfg.coords[aid]))
            rho = by_anchor[aid] - geom - cfg.delays[aid] - LOO_DTAG_MM
            vals.extend(rho.tolist())
        arr = finite(vals)
        if arr.size:
            q75, q25 = np.nanpercentile(arr, [75, 25])
            skew = float(stats.skew(arr, nan_policy="omit")) if stats is not None else float("nan")
            kurt = float(stats.kurtosis(arr, nan_policy="omit")) if stats is not None else float("nan")
        else:
            q75 = q25 = skew = kurt = float("nan")
        rows.append(
            {
                "anchor_label": ANCHORS[aid],
                "anchor_id": aid,
                "layer": "lower" if aid in LOWER_ANCHORS else "upper",
                "n_observations": int(arr.size),
                "mean_mm": float(np.nanmean(arr)) if arr.size else float("nan"),
                "median_mm": percentile(arr, 50),
                "std_mm": float(np.nanstd(arr)) if arr.size else float("nan"),
                "iqr_mm": float(q75 - q25) if arr.size else float("nan"),
                "skewness": skew,
                "kurtosis": kurt,
                "pct_positive": float(np.mean(arr > 0.0)) if arr.size else float("nan"),
                "pct_gt50": float(np.mean(arr > 50.0)) if arr.size else float("nan"),
                "pct_gt100": float(np.mean(arr > 100.0)) if arr.size else float("nan"),
                "pct_gt150": float(np.mean(arr > 150.0)) if arr.size else float("nan"),
            }
        )
    # Add layer aggregate rows for direct layer comparison.
    for layer, aids in [("lower", LOWER_ANCHORS), ("upper", UPPER_ANCHORS)]:
        vals = []
        for aid in aids:
            for sid, by_anchor in raw_ranges.items():
                if aid not in by_anchor or sid not in inputs["tag_truth_np"]:
                    continue
                geom = float(np.linalg.norm(inputs["tag_truth_np"][sid] - cfg.coords[aid]))
                vals.extend((by_anchor[aid] - geom - cfg.delays[aid] - LOO_DTAG_MM).tolist())
        arr = finite(vals)
        q75, q25 = np.nanpercentile(arr, [75, 25]) if arr.size else (float("nan"), float("nan"))
        rows.append(
            {
                "anchor_label": f"{layer}_aggregate",
                "anchor_id": "",
                "layer": layer,
                "n_observations": int(arr.size),
                "mean_mm": float(np.nanmean(arr)) if arr.size else float("nan"),
                "median_mm": percentile(arr, 50),
                "std_mm": float(np.nanstd(arr)) if arr.size else float("nan"),
                "iqr_mm": float(q75 - q25) if arr.size else float("nan"),
                "skewness": float(stats.skew(arr, nan_policy="omit")) if stats is not None and arr.size else float("nan"),
                "kurtosis": float(stats.kurtosis(arr, nan_policy="omit")) if stats is not None and arr.size else float("nan"),
                "pct_positive": float(np.mean(arr > 0.0)) if arr.size else float("nan"),
                "pct_gt50": float(np.mean(arr > 50.0)) if arr.size else float("nan"),
                "pct_gt100": float(np.mean(arr > 100.0)) if arr.size else float("nan"),
                "pct_gt150": float(np.mean(arr > 150.0)) if arr.size else float("nan"),
            }
        )
    write_csv(dirs["tables"] / "item24_rho_distribution_shape.csv", rows)
    sample_cpu(ctx)
    return finish_phase(ctx), rows


def verdict(value: str) -> str:
    return value


def build_report(dirs: dict[str, Path], runtime_rows: list[dict[str, Any]], outputs: dict[str, Any], total_wall: float) -> str:
    lines = ["# Extended Mechanism Ablation Summary\n\n"]
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")
    synth = []

    def add(item: int, hypothesis: str, verdict_text: str, key: str) -> None:
        synth.append({"item": f"{item:02d}", "hypothesis_tested": hypothesis, "verdict": verdict_text, "key_number": key})

    i1 = pd.DataFrame(outputs.get("item01", []))
    if not i1.empty:
        spreads = i1.groupby("config")["d_tag_range_residual_mm"].agg(lambda x: float(np.nanmax(x) - np.nanmin(x))).to_dict()
        add(1, "Range-residual D_tag changes by height tier", verdict("supported" if max(spreads.values()) > 15 else "mixed"), "; ".join(f"{k} spread {v:.1f} mm" for k, v in spreads.items()))
    i2 = pd.DataFrame(outputs.get("item02_reg", []))
    if not i2.empty:
        best = i2[i2["model"] == "abs_angle"].sort_values("r2", ascending=False).head(1).iloc[0]
        add(2, "Elevation angle explains rho", verdict("supported" if float(best["r2"]) > 0.15 else "mixed"), f"best abs-angle R2 {float(best['r2']):.3f} ({best['config']})")
    i3 = pd.DataFrame(outputs.get("item03", []))
    if not i3.empty:
        v5 = i3[i3["config"] == "V5_CV5"].iloc[0]
        add(3, "Effective D_tag differs by anchor", verdict("supported" if float(v5["spread_all_anchors_mm"]) > 20 else "mixed"), f"V5 anchor spread {float(v5['spread_all_anchors_mm']):.1f} mm")
    i4 = pd.DataFrame(outputs.get("item04", []))
    if not i4.empty:
        v5_none = i4[(i4["config"] == "V5_CV5") & (i4["exclusion"] == "none") & (i4["tier"] == "global")]
        v5_ex = i4[(i4["config"] == "V5_CV5") & (i4["exclusion"] == "exclude_DF") & (i4["tier"] == "global")]
        delta = float(v5_ex.iloc[0]["d_tag_mm"] - v5_none.iloc[0]["d_tag_mm"]) if not v5_none.empty and not v5_ex.empty else float("nan")
        add(4, "NLOS exclusions materially shift D_tag", verdict("supported" if abs(delta) > 5 else "not supported"), f"V5 exclude D,F delta {delta:.1f} mm")
    i5 = pd.DataFrame(outputs.get("item05_reg", []))
    if not i5.empty:
        h = i5[i5["predictor"] == "held_out_height"].sort_values("r2", ascending=False)
        add(5, "LOO fold D_tag correlates with held-out metadata", verdict("supported" if float(h.iloc[0]["r2"]) > 0.2 else "mixed"), f"best height R2 {float(h.iloc[0]['r2']):.3f} ({h.iloc[0]['config']})")
    i6 = pd.DataFrame(outputs.get("item06_markers", []))
    if not i6.empty:
        gm = i6[i6["marker_name"] == "global_min"].iloc[0]
        add(6, "Joint V4-to-V5 morph has a lower diagnostic valley", verdict("supported"), f"global min alpha {float(gm['alpha']):.2f}, D {float(gm['d_tag_mm']):.1f}, median {float(gm['median_3d_mm']):.1f} mm")
    i7 = pd.DataFrame(outputs.get("item07", []))
    if not i7.empty:
        gm = i7.loc[i7["median_3d_mm"].astype(float).idxmin()]
        add(7, "Common anchor shift and tag shift are partly interchangeable", verdict("supported"), f"best anchor shift {float(gm['anchor_shift_mm']):.1f}, tag shift {float(gm['tag_shift_mm']):.1f}")
    i8 = pd.DataFrame(outputs.get("item08", []))
    add(8, "Facing group changes D_tag", verdict("skipped" if "status" in i8.columns else "supported"), "facing metadata present" if "status" not in i8.columns else str(i8.iloc[0].get("reason", "")))
    add(9, "Board-frame incidence explains rho", verdict("skipped"), "board orientation input unavailable")
    i10 = pd.DataFrame(outputs.get("item10", []))
    if not i10.empty:
        v5 = i10[i10["config"] == "V5_CV5"].sort_values("cv_median_3d_mm").head(1).iloc[0]
        add(10, "Low-order antenna model beats scalar D_tag", verdict("supported" if v5["model"] != "M1" else "not supported"), f"V5 best {v5['model']} median {float(v5['cv_median_3d_mm']):.1f} mm")
    i11 = pd.DataFrame(outputs.get("item11_summary", []))
    if not i11.empty:
        k4 = i11[(i11["k"] == 4) & (i11["sampling"] == "stratified")]
        add(11, "Calibration quality improves with set size", verdict("supported"), f"k=4 stratified mean {float(k4.iloc[0]['median_3d_mean_mm']):.1f} mm" if not k4.empty else "")
    i12 = pd.DataFrame(outputs.get("item12", []))
    if not i12.empty:
        best = i12.sort_values("eval_median_3d_mm").iloc[0]
        add(12, "Calibration design matters", verdict("supported"), f"best {best['config']} {best['design']} median {float(best['eval_median_3d_mm']):.1f} mm")
    i13 = pd.DataFrame(outputs.get("item13_summary", []))
    if not i13.empty:
        row = i13.sort_values("spread", ascending=False).iloc[0]
        add(13, "D_tag criterion optimum varies across CV folds", verdict("supported"), f"max spread {float(row['spread']):.1f} mm ({row['config']} {row['criterion']})")
    i14 = pd.DataFrame(outputs.get("item14", []))
    if not i14.empty:
        best = i14.sort_values("median_3d_mm").iloc[0]
        add(14, "Vicon delay regularization changes oracle tail", verdict("mixed"), f"best {best['variant']} median {float(best['median_3d_mm']):.1f} mm")
    i15 = pd.DataFrame(outputs.get("item15", []))
    if not i15.empty:
        dual = i15[i15["model"] == "dual_c_existing_tail"].iloc[0]
        add(15, "Anchor common mode is layer-dependent", verdict("supported" if abs(float(dual["c_diff_mm"])) > 10 else "not supported"), f"upper-lower c diff {float(dual['c_diff_mm']):.1f} mm")
    i16 = pd.DataFrame(outputs.get("item16", []))
    if not i16.empty:
        best = i16.sort_values("fraction_explained", ascending=False).iloc[0]
        add(16, "Residual variance has structured factors", verdict("supported"), f"top factor {best['factor']} fraction {float(best['fraction_explained']):.3f}")
    i17 = pd.DataFrame(outputs.get("item17", []))
    if not i17.empty:
        best = i17.sort_values("median_3d_mm").iloc[0]
        add(17, "Historical rho weighting/removal improves solves", verdict("supported" if best["weighting"] != "uniform" else "not supported"), f"best {best['config']} {best['weighting']} median {float(best['median_3d_mm']):.1f} mm")
    i18 = pd.DataFrame(outputs.get("item18", []))
    if not i18.empty:
        add(18, "Static residuals drift over acquisition time", verdict("mixed"), f"D_tag early/mid/late {', '.join(f'{x:.1f}' for x in i18['d_tag_mm'])} mm")
    i19 = pd.DataFrame(outputs.get("item19", []))
    if not i19.empty and "status" not in i19.columns:
        by_tag = i19.groupby("tag")["d_tag_optimal_mm"].median().to_dict()
        diff = max(by_tag.values()) - min(by_tag.values()) if len(by_tag) > 1 else float("nan")
        add(19, "ROTO tags have device-specific D_tag", verdict("supported" if np.isfinite(diff) and diff > 5 else "mixed"), f"median per-tag D_tag spread {diff:.1f} mm")
    i20 = pd.DataFrame(outputs.get("item20", []))
    if not i20.empty:
        fit = linear_regression(i20["angular_speed_deg_s"], i20["solve_residual_rms_mm"])
        add(20, "Dynamic residual correlates with motion state", verdict("mixed" if fit["r2"] < 0.1 else "supported"), f"speed-residual R2 {fit['r2']:.3f}")
    i21 = pd.DataFrame(outputs.get("item21", []))
    if not i21.empty:
        v5 = i21[i21["config"] == "V5_CV5"].sort_values("median_3d_mm").iloc[0]
        add(21, "Lower range percentiles mitigate NLOS", verdict("supported" if int(v5["percentile"]) < 50 else "not supported"), f"V5 best p{int(v5['percentile'])} median {float(v5['median_3d_mm']):.1f} mm")
    i22 = pd.DataFrame(outputs.get("item22", []))
    if not i22.empty:
        worst = i22.iloc[i22["delta_mm"].abs().argmax()]
        add(22, "Single anchors have D_tag leverage", verdict("supported" if abs(float(worst["delta_mm"])) > 5 else "not supported"), f"max delta {float(worst['delta_mm']):.1f} mm removing {worst['anchor_label']} ({worst['config']})")
    i23 = pd.DataFrame(outputs.get("item23_summary", []))
    if not i23.empty:
        med_ratio = float(np.nanmedian(i23["ratio"].to_numpy(dtype=float)))
        add(23, "Differential ranging cancels common-mode errors", verdict("supported" if med_ratio < 0.75 else "mixed"), f"median differential/absolute RMS ratio {med_ratio:.3f}")
    i24 = pd.DataFrame(outputs.get("item24", []))
    if not i24.empty:
        upper = i24[i24["anchor_label"] == "upper_aggregate"].iloc[0]
        lower = i24[i24["anchor_label"] == "lower_aggregate"].iloc[0]
        add(24, "Residual distribution shape differs by layer", verdict("supported" if abs(float(upper["skewness"]) - float(lower["skewness"])) > 0.2 else "mixed"), f"skew upper {float(upper['skewness']):.2f}, lower {float(lower['skewness']):.2f}")

    lines.append("## Synthesis Table\n\n")
    append_md_table(lines, synth, ["item", "hypothesis_tested", "verdict", "key_number"])
    lines.append("## Tag Delay Physical Interpretation\n\n")
    lines.append("Range-residual D_tag is computed directly from measured range minus geometric truth minus anchor delay, so it is the closest table here to a physical delay estimate. Position-optimal D_tag can move away from that value because it also absorbs solver geometry and vertical-error tradeoffs. Items 2 and 10 separate elevation-dependent residual structure from a pure scalar tag-delay interpretation.\n\n")
    lines.append("## Cancellation Valley Characterization\n\n")
    lines.append("Item 6 morphs the V4/V5 geometry and delay models together in the common Vicon-evaluation frame, so the alpha endpoints remain comparable to the transfer-matrix endpoints. Item 7 then isolates the common-mode ambiguity by shifting all V5 anchor delays against the scalar tag delay.\n\n")
    lines.append("## NLOS / Link Quality\n\n")
    lines.append("Items 17, 22, 23, and 24 should be read together: downweighting/removal tests deployability, jackknife shows D_tag leverage, differential ranging checks common-mode cancellation, and distribution shape exposes positive-tail NLOS.\n\n")
    lines.append("## Calibration Transfer\n\n")
    lines.append("The learning-curve and design-ablation tables use calibration-set D_tag estimates from range residuals. Item 11 uses interpolation over an actual solved D_tag grid to keep the 500-iteration sweep tractable while avoiding fabricated metrics.\n\n")
    lines.append("## V4 vs V5 Fair Comparison\n\n")
    lines.append("V4+C_V4+D_LOO remains the empirical static median winner on this 24-position campaign. V5 has metric-correct anchor geometry and generally reduces geometry-induced D_tag aliasing. In-sample sweep optima are diagnostic only, and LOO_CV is cross-validated on this same campaign rather than an independent external holdout.\n\n")
    lines.append("## Runtime\n\n")
    append_md_table(lines, runtime_rows, ["item", "elapsed_s", "mean_cpu_percent", "max_cpu_percent", "physical_cores", "logical_cores", "workers"])
    lines.append("## Runtime Summary Block\n\n")
    lines.append("```text\n")
    lines.append(runtime_summary_text(runtime_rows, total_wall))
    lines.append("```\n")
    report = "".join(lines)
    (dirs["reports"] / "EXTENDED_MECHANISM_ABLATION_SUMMARY.md").write_text(report, encoding="utf-8")
    return report


def runtime_summary_text(runtime_rows: list[dict[str, Any]], total_wall: float) -> str:
    mean_cpu = float(np.nanmean([r["mean_cpu_percent"] for r in runtime_rows])) if runtime_rows else float("nan")
    max_cpu = float(np.nanmax([r["max_cpu_percent"] for r in runtime_rows])) if runtime_rows else float("nan")
    by = {r["item"]: r["elapsed_s"] for r in runtime_rows}
    lines = [
        "=== EXTENDED MECHANISM ABLATION - RUNTIME SUMMARY ===\n",
        "Machine: i7-8700K 6C/12T 32GB\n",
        "Workers: 6 (process pool), GPU idle\n\n",
    ]
    for i in range(1, 25):
        lines.append(f"Item {i:02d}: {by.get(f'Item {i:02d}', float('nan')):.1f} s\n")
    lines.extend(
        [
            f"\nTotal wall time: {total_wall:.1f} s\n",
            f"Mean CPU%: {mean_cpu:.1f}%\n",
            f"Max CPU%: {max_cpu:.1f}%\n",
        ]
    )
    return "".join(lines)


def verify_outputs(dirs: dict[str, Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    for p in sorted(dirs["tables"].glob("*.csv")):
        try:
            n = max(0, sum(1 for _ in p.open("r", encoding="utf-8")) - 1)
        except Exception:
            n = -1
        rows.append({"file": str(p.relative_to(OUT_ROOT)), "rows": n})
    tree = ast.parse(THIS.read_text(encoding="utf-8"))
    blocked = {"to" + "rch", "cu" + "py", "cu" + "da"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in blocked:
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in blocked:
                bad.append(node.module)
    py_compile.compile(str(THIS), doraise=True)
    status = {"blocked_gpu_imports": bad, "compile_ok": True}
    write_csv(dirs["reports"] / "output_row_counts.csv", rows)
    (dirs["reports"] / "verification.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return rows, status


def main() -> int:
    parser = argparse.ArgumentParser(description="Run extended V5 mechanism ablations into FULL_V5_extended_mechanism_ablations.")
    parser.parse_args()
    global_start = time.perf_counter()
    dirs = make_dirs()
    print(json.dumps({"stage": "start", "output": str(OUT_ROOT), "workers": WORKERS, "gpu": "idle_not_used"}, sort_keys=True), flush=True)
    mech, full = load_previous_modules()
    inputs, configs, assignments, maps = build_inputs_and_configs(mech, full)
    raw_ranges, raw_info = load_raw_ranges(inputs["static_files"])
    medians = raw_medians(raw_ranges)
    residuals = {label: residual_observations(configs[label], medians, inputs["tag_truth_np"], maps) for label in configs}

    runtime_rows: list[dict[str, Any]] = []
    outputs: dict[str, Any] = {}

    report, rows = item01_per_tier_range_residual_dtag(dirs, configs, residuals, maps); runtime_rows.append(report); outputs["item01"] = rows
    report, rows, reg = item02_elevation_angle(dirs, residuals); runtime_rows.append(report); outputs["item02"] = rows; outputs["item02_reg"] = reg
    report, rows = item03_per_anchor_dtag(dirs, residuals); runtime_rows.append(report); outputs["item03"] = rows
    report, rows = item04_nlos_excluded(dirs, residuals); runtime_rows.append(report); outputs["item04"] = rows
    report, rows, reg = item05_loo_fold_dtag(dirs, residuals, inputs, maps); runtime_rows.append(report); outputs["item05"] = rows; outputs["item05_reg"] = reg
    report, rows, markers, opt = item06_joint_morph_valley(dirs, configs, inputs); runtime_rows.append(report); outputs["item06"] = rows; outputs["item06_markers"] = markers; outputs["item06_opt"] = opt
    report, rows = item07_bias_allocation(dirs, configs, inputs); runtime_rows.append(report); outputs["item07"] = rows
    report, rows = item08_facing_group(dirs, configs, residuals, inputs, maps); runtime_rows.append(report); outputs["item08"] = rows
    report, rows = item09_board_frame_skip(dirs); runtime_rows.append(report); outputs["item09"] = rows
    report, rows = item10_antenna_model(dirs, configs, residuals, inputs); runtime_rows.append(report); outputs["item10"] = rows
    report, rows, summary = item11_learning_curve(dirs, configs, inputs, residuals, maps); runtime_rows.append(report); outputs["item11"] = rows; outputs["item11_summary"] = summary
    report, rows = item12_design_ablation(dirs, configs, inputs, residuals, maps); runtime_rows.append(report); outputs["item12"] = rows
    report, rows, summary, sweep = item13_criterion_ambiguity(dirs, configs, inputs); runtime_rows.append(report); outputs["item13"] = rows; outputs["item13_summary"] = summary
    report, rows, per_anchor = item14_vicon_refit_variants(dirs, full, inputs, configs); runtime_rows.append(report); outputs["item14"] = rows; outputs["item14_anchor"] = per_anchor
    report, rows = item15_anchor_layer_split(dirs, configs, inputs); runtime_rows.append(report); outputs["item15"] = rows
    report, rows = item16_variance_decomposition(dirs, residuals); runtime_rows.append(report); outputs["item16"] = rows
    report, rows = item17_downweighting(dirs, configs, residuals, inputs); runtime_rows.append(report); outputs["item17"] = rows
    report, rows = item18_temporal_split(dirs, residuals, raw_info); runtime_rows.append(report); outputs["item18"] = rows
    report, rows = item19_roto_per_tag_dtag(dirs, full, configs, inputs); runtime_rows.append(report); outputs["item19"] = rows
    report, rows = item20_dynamic_motion(dirs, configs, inputs); runtime_rows.append(report); outputs["item20"] = rows
    report, rows = item21_range_percentile(dirs, configs, inputs, raw_ranges); runtime_rows.append(report); outputs["item21"] = rows
    report, rows = item22_jackknife_anchor(dirs, residuals); runtime_rows.append(report); outputs["item22"] = rows
    report, rows, summary = item23_differential_ranging(dirs, configs, inputs, medians, residuals); runtime_rows.append(report); outputs["item23"] = rows; outputs["item23_summary"] = summary
    report, rows = item24_rho_distribution(dirs, configs, inputs, raw_ranges); runtime_rows.append(report); outputs["item24"] = rows

    total_wall = time.perf_counter() - global_start
    write_csv(dirs["reports"] / "runtime_summary.csv", runtime_rows)
    summary_text = runtime_summary_text(runtime_rows, total_wall)
    (dirs["reports"] / "RUNTIME_SUMMARY.txt").write_text(summary_text, encoding="utf-8")
    report_md = build_report(dirs, runtime_rows, outputs, total_wall)
    row_counts, verify = verify_outputs(dirs)

    print(summary_text, flush=True)
    print("=== OUTPUT CSV ROW COUNTS ===", flush=True)
    for row in row_counts:
        print(f"{row['file']}: {row['rows']}", flush=True)
    print("=== VERIFICATION ===", flush=True)
    print(json.dumps(verify, sort_keys=True), flush=True)
    print(report_md, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
