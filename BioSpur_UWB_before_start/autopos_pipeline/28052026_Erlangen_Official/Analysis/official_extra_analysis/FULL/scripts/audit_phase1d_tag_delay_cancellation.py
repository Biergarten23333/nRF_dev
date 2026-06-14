#!/usr/bin/env python3
"""Phase 1d: tag-delay cancellation tests.

This diagnostic writes audit-only outputs. It does not edit paper text or
production layouts.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ANCHORS = list("ABCDEFGH")
STATIC_TAG = "BSF66F"
ROTO_TAGS = ["BS2DCE", "BSDC91"]
FIXED_TAG_DELAYS = [0, 40, 60, 80, 95, 112, 130, 150]
DTAG_BOUND_MM = 300.0
HUBER_K = 2.0
MAX_WORKERS = max(2, min(8, os.cpu_count() or 2))

THIS = Path(__file__).resolve()
FULL_ROOT = THIS.parents[1]
ANALYSIS_ROOT = THIS.parents[3]
OFFICIAL_ROOT = THIS.parents[4]
REPO_ROOT = OFFICIAL_ROOT.parents[1]
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"

sys.path.insert(0, str(FULL_ROOT / "scripts"))
sys.path.insert(0, str(SOLVER_ROOT))

from tag_ground_truth import load_corrected_static_truth  # noqa: E402
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json  # noqa: E402
from biospur_tag_positioning_offline_solver.models import Frame, Layout, SolverConfig  # noqa: E402


OUT = FULL_ROOT / "audit_phase1d"
TABLES = OUT / "tables"
FIGS = OUT / "figs"

CURRENT_LAYOUT = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json"
CURRENT_SIGMA = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check/tables/anchor_sigma.json"
PHASE1C_LAYOUT = FULL_ROOT / "audit_phase1c/layouts/v4io_common_mode/layout.json"
PHASE1C_SIGMA = FULL_ROOT / "audit_phase1c/layouts/tables/anchor_sigma.json"
PHASE1_SUMMARY = FULL_ROOT / "audit_phase1/tables/audit_phase1_revised_summary.json"
PHASE1C_SUMMARY = FULL_ROOT / "audit_phase1c/tables/audit_phase1c_summary.json"
PHASE1C_ORACLE_ANCHORS = FULL_ROOT / "audit_phase1c/tables/item1_oracle_per_anchor_delay.csv"
PHASE1C_COMMON_ANCHORS = FULL_ROOT / "audit_phase1c/tables/item2_common_mode_anchor_delays.csv"
CURRENT_STATIC_SUMMARY = (
    ANALYSIS_ROOT
    / "official_extra_analysis/FULL_4way_comparison/production_method_probe/production_static_method_real_run_eval/tables/tag_accuracy_summary.csv"
)
CURRENT_ROTO_SUMMARY = FULL_ROOT / "roto_absolute/tables/roto_abs_summary_by_solver.csv"
STATIC_METADATA_CSV = OFFICIAL_ROOT / "solver/work/field_dataset_staged/FULL-COMPARE-1000-production-T4-real/tables/static_all_captures.csv"
CAPTURES_ROOT = OFFICIAL_ROOT / "captures/erlangen_20260528_optitrack"
OPTI_ROOT = OFFICIAL_ROOT / "opti_captures/full"
ROTO_ABS_SCRIPT = FULL_ROOT / "roto_absolute/scripts/run_roto_absolute_analysis.py"


@dataclass(frozen=True)
class Fit:
    rotation: np.ndarray
    translation: np.ndarray
    scale: float
    det: float
    aligned: np.ndarray


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


def dump_json(path: Path, obj: Any) -> None:
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


def percentile(values: list[float] | np.ndarray, pct: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, pct))


def rmse(values: list[float] | np.ndarray) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(math.sqrt(np.mean(arr * arr)))


def iqr(values: list[float] | np.ndarray) -> float:
    return percentile(values, 75) - percentile(values, 25)


def fmt_case_tag_delay(value: float | None) -> str:
    if value is None:
        return "joint"
    return f"fixed_{value:.0f}mm"


def session_id_from_path(path: Path) -> str:
    for parent in path.parents:
        m = re.search(r"(static_ID\d+)_", parent.name)
        if m:
            return m.group(1).replace("static_", "")
    return path.parents[1].name


def capture_name_from_path(path: Path) -> str:
    for parent in path.parents:
        if parent.name.startswith("static_ID"):
            return parent.name
    return path.parents[1].name


def load_static_metadata(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        sid = str(row.get("ID", "")).strip()
        if sid and sid not in out:
            out[sid] = {
                "location": row.get("location", ""),
                "height": row.get("height", ""),
                "facing": row.get("facing", ""),
            }
    return out


def load_layout_coords(path: Path) -> tuple[list[str], np.ndarray, np.ndarray, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    labels = [a.get("label", ANCHORS[int(a["id"])]) for a in data["anchors"]]
    coords = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in data["anchors"]], dtype=float)
    delays = np.array([float(a.get("d_anchor_mm") or 0.0) for a in data["anchors"]], dtype=float)
    tag_delay = float(data.get("tag_delay_mm") or 0.0)
    return labels, coords, delays, tag_delay


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_reflection: bool, allow_scale: bool) -> Fit:
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
    return Fit(rotation=r, translation=t, scale=scale, det=float(np.linalg.det(r)), aligned=aligned)


def apply_fit(points: np.ndarray, fit: Fit) -> np.ndarray:
    return fit.scale * points @ fit.rotation + fit.translation


def filter_frames(frames: list[Frame], min_anchors: int = 4) -> list[Frame]:
    out: list[Frame] = []
    for frame in frames:
        obs = tuple(o for o in frame.observations if 0 <= o.anchor_id < 8)
        if len(obs) >= min_anchors:
            out.append(
                Frame(
                    tag=frame.tag,
                    sweep=frame.sweep,
                    host_elapsed_s=frame.host_elapsed_s,
                    host_epoch_s=frame.host_epoch_s,
                    observations=obs,
                    imu=frame.imu,
                )
            )
    return out


def huber_weight(rn: float, k: float = HUBER_K) -> float:
    ar = abs(rn)
    if ar <= k:
        return 1.0
    return k / max(ar, 1e-12)


def joint_solve_frame(
    layout: Layout,
    frame: Frame,
    *,
    x0: np.ndarray | None = None,
    dtag_init_mm: float = 0.0,
    dtag_bound_mm: float = DTAG_BOUND_MM,
    max_iters: int = 12,
) -> dict | None:
    obs = [o for o in frame.observations if o.anchor_id in layout.anchors and o.range_mm > 0.0]
    if len(obs) < 4:
        return None
    anchors = np.array([[layout.anchors[o.anchor_id].x_mm, layout.anchors[o.anchor_id].y_mm, layout.anchors[o.anchor_id].z_mm] for o in obs], dtype=float)
    ranges = np.array([float(o.range_mm) for o in obs], dtype=float)
    delays = np.array([float(layout.anchors[o.anchor_id].d_anchor_mm) for o in obs], dtype=float)
    sigmas = np.array([max(5.0, float(layout.anchors[o.anchor_id].sigma_mm)) for o in obs], dtype=float)
    if x0 is not None and np.isfinite(x0).all():
        p = np.asarray(x0, dtype=float).copy()
    else:
        p = np.nanmean(anchors, axis=0)
    dtag = float(np.clip(dtag_init_mm, -dtag_bound_mm, dtag_bound_mm))

    iterations = 0
    for it in range(max_iters):
        h = np.eye(4, dtype=float) * 1e-9
        g = np.zeros(4, dtype=float)
        rows = 0
        for anchor, measured, delay, sigma in zip(anchors, ranges, delays, sigmas):
            diff = p - anchor
            dist = float(np.linalg.norm(diff))
            if dist < 1e-6:
                continue
            residual = dist + delay + dtag - measured
            rn = residual / sigma
            sw = math.sqrt(huber_weight(float(rn)))
            j = np.empty(4, dtype=float)
            j[:3] = diff / dist / sigma * sw
            j[3] = 1.0 / sigma * sw
            r = rn * sw
            g += j * r
            h += np.outer(j, j)
            rows += 1
        if rows < 4:
            return None
        try:
            step = np.linalg.solve(h, -g)
        except np.linalg.LinAlgError:
            step, *_ = np.linalg.lstsq(h, -g, rcond=None)
        if not np.isfinite(step).all():
            return None
        pos_norm = float(np.linalg.norm(step[:3]))
        if pos_norm > 500.0:
            step[:3] *= 500.0 / pos_norm
        if abs(float(step[3])) > 200.0:
            step[3] = math.copysign(200.0, float(step[3]))
        p += step[:3]
        dtag = float(np.clip(dtag + step[3], -dtag_bound_mm, dtag_bound_mm))
        iterations = it + 1
        if float(np.linalg.norm(step[:3])) < 0.02 and abs(float(step[3])) < 0.02:
            break

    raw_residuals = []
    for anchor, measured, delay in zip(anchors, ranges, delays):
        raw_residuals.append(float(np.linalg.norm(p - anchor) + delay + dtag - measured))
    res = np.asarray(raw_residuals, dtype=float)
    return {
        "x_mm": float(p[0]),
        "y_mm": float(p[1]),
        "z_mm": float(p[2]),
        "d_tag_mm": float(dtag),
        "anchors_input": int(len(obs)),
        "anchors_used": int(len(obs)),
        "iterations": int(iterations),
        "residual_rms_mm": float(math.sqrt(np.mean(res * res))),
        "residual_p95_abs_mm": percentile(np.abs(res), 95),
        "max_abs_residual_mm": float(np.max(np.abs(res))),
    }


def solve_static_fixed(layout: Layout, frames: list[Frame], fixed_tag_delay_mm: float) -> list[dict]:
    solver = TagPositionSolver(layout, SolverConfig(method="T4"), tag_delay_by_tag={STATIC_TAG: fixed_tag_delay_mm})
    rows: list[dict] = []
    for frame in frames:
        result = solver.solve_frame(frame)
        if result is None or result.status != "ok":
            continue
        rows.append(
            {
                "x_mm": float(result.x_mm),
                "y_mm": float(result.y_mm),
                "z_mm": float(result.z_mm),
                "d_tag_mm": float(fixed_tag_delay_mm),
                "anchors_input": int(result.anchors_input),
                "anchors_used": int(result.anchors_used),
                "iterations": "",
                "residual_rms_mm": float(result.residual_rms_mm),
                "residual_p95_abs_mm": float(result.residual_p95_abs_mm),
                "max_abs_residual_mm": float(result.max_abs_residual_mm),
            }
        )
    return rows


def solve_static_joint(layout: Layout, frames: list[Frame]) -> list[dict]:
    rows: list[dict] = []
    for frame in frames:
        result = joint_solve_frame(layout, frame, dtag_init_mm=0.0)
        if result is None:
            continue
        rows.append(result)
    return rows


def summarize_frame_results(results: list[dict], point_estimator: str = "mean") -> dict:
    if not results:
        return {"status": "no_solution", "frames_solved": 0, "x_mm": float("nan"), "y_mm": float("nan"), "z_mm": float("nan")}
    pts = np.array([[r["x_mm"], r["y_mm"], r["z_mm"]] for r in results], dtype=float)
    if point_estimator == "mean":
        p = np.nanmean(pts, axis=0)
    elif point_estimator == "median":
        p = np.nanmedian(pts, axis=0)
    else:
        raise ValueError(point_estimator)
    d = pts - p[None, :]
    d3 = np.linalg.norm(d, axis=1)
    residual = np.array([r["residual_rms_mm"] for r in results], dtype=float)
    anchors_input = np.array([r["anchors_input"] for r in results], dtype=float)
    anchors_used = np.array([r["anchors_used"] for r in results], dtype=float)
    dtag = np.array([r["d_tag_mm"] for r in results], dtype=float)
    return {
        "status": "ok",
        "frames_solved": int(len(results)),
        "x_mm": float(p[0]),
        "y_mm": float(p[1]),
        "z_mm": float(p[2]),
        "mean_x_mm": float(np.nanmean(pts[:, 0])),
        "mean_y_mm": float(np.nanmean(pts[:, 1])),
        "mean_z_mm": float(np.nanmean(pts[:, 2])),
        "median_x_mm": float(np.nanmedian(pts[:, 0])),
        "median_y_mm": float(np.nanmedian(pts[:, 1])),
        "median_z_mm": float(np.nanmedian(pts[:, 2])),
        "x_std_mm": float(np.nanstd(d[:, 0])),
        "y_std_mm": float(np.nanstd(d[:, 1])),
        "z_std_mm": float(np.nanstd(d[:, 2])),
        "d3_std_mm": rmse(d3),
        "radial_p50_mm": percentile(d3, 50),
        "radial_p95_mm": percentile(d3, 95),
        "anchors_input_median": percentile(anchors_input, 50),
        "anchors_used_median": percentile(anchors_used, 50),
        "pct_solved_ge7": float(np.mean(anchors_input >= 7.0) * 100.0),
        "pct_solved_ge8": float(np.mean(anchors_input >= 8.0) * 100.0),
        "residual_rms_median_mm": percentile(residual, 50),
        "residual_rms_p95_mm": percentile(residual, 95),
        "d_tag_frame_median_mm": percentile(dtag, 50),
        "d_tag_frame_p25_mm": percentile(dtag, 25),
        "d_tag_frame_p75_mm": percentile(dtag, 75),
        "d_tag_frame_iqr_mm": iqr(dtag),
        "d_tag_frame_p5_mm": percentile(dtag, 5),
        "d_tag_frame_p95_mm": percentile(dtag, 95),
        "d_tag_frame_mean_mm": float(np.nanmean(dtag)),
        "d_tag_frame_std_mm": float(np.nanstd(dtag)),
    }


def evaluate_static_case(job: dict) -> dict:
    layout_name = str(job["layout_name"])
    layout_path = Path(job["layout_path"])
    sigma_path = Path(job["sigma_path"])
    case_kind = str(job["case_kind"])
    fixed_tag_delay_mm = job.get("fixed_tag_delay_mm")
    point_estimator = str(job.get("point_estimator", "mean"))

    layout = load_layout_json(layout_path, sigma_path)
    labels, coords, _delays, _layout_tag_delay = load_layout_coords(layout_path)
    anchor_truth, tag_truth, tag_truth_meta, _correction_rows = load_corrected_static_truth(OPTI_ROOT, ANCHORS, ["ID01", "ID02", "ID03", "ID04", "ID05"])
    truth_coords = np.vstack([anchor_truth[a] for a in ANCHORS])
    src = np.vstack([coords[labels.index(a)] for a in ANCHORS])
    fit = fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=False)
    sim = fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=True)
    anchor_centroid = truth_coords.mean(axis=0)
    metadata = load_static_metadata(STATIC_METADATA_CSV)
    static_files = sorted(CAPTURES_ROOT.glob("static_ID*/tag_capture*/tr_all.csv"))

    session_rows: list[dict] = []
    frame_diag_rows: list[dict] = []
    for path in static_files:
        sid = session_id_from_path(path)
        frames = read_tr_all_frames(path, tags={STATIC_TAG}, min_anchors=4)
        frames = filter_frames(sorted(frames, key=lambda f: (float(f.host_elapsed_s), int(f.sweep))), min_anchors=4)
        if case_kind == "joint":
            results = solve_static_joint(layout, frames)
        elif case_kind == "fixed":
            results = solve_static_fixed(layout, frames, float(fixed_tag_delay_mm))
        else:
            raise ValueError(case_kind)
        summary = summarize_frame_results(results, point_estimator=point_estimator)
        truth = tag_truth.get(sid)
        if truth is None or summary["status"] != "ok":
            continue
        point = np.array([[summary["x_mm"], summary["y_mm"], summary["z_mm"]]], dtype=float)
        aligned = apply_fit(point, fit)[0]
        diff = aligned - truth
        meta = metadata.get(sid, {})
        truth_info = tag_truth_meta.get(sid, {})
        case_id = f"{layout_name}_{fmt_case_tag_delay(fixed_tag_delay_mm if case_kind == 'fixed' else None)}"
        session_rows.append(
            {
                "case_id": case_id,
                "layout_name": layout_name,
                "case_kind": case_kind,
                "fixed_tag_delay_mm": "" if fixed_tag_delay_mm is None else float(fixed_tag_delay_mm),
                "tag_method": "T4_joint_dtag" if case_kind == "joint" else "T4_fixed_dtag",
                "point_estimator": point_estimator,
                "ID": sid,
                "capture": capture_name_from_path(path),
                "location": meta.get("location", ""),
                "height": meta.get("height", ""),
                "facing": meta.get("facing", ""),
                "tag_truth_source": truth_info.get("tag_truth_source", ""),
                "tag_truth_corrected": truth_info.get("tag_truth_corrected", False),
                "frames_input": int(len(frames)),
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
                "anchor_fit_det": fit.det,
                "anchor_fit_scale": fit.scale,
                "anchor_similarity_scale_diagnostic": sim.scale,
                "distance_to_array_centroid_mm": float(np.linalg.norm(truth - anchor_centroid)),
                "scale_bias_expected_mm": float(abs(1.0 - sim.scale) * np.linalg.norm(truth - anchor_centroid)),
                "source_tr_all": str(path),
                "layout_json": str(layout_path),
            }
        )
        if case_kind == "joint":
            for idx, result in enumerate(results):
                if idx % 20 == 0:
                    frame_diag_rows.append(
                        {
                            "case_id": case_id,
                            "layout_name": layout_name,
                            "ID": sid,
                            "frame_index": idx,
                            "d_tag_mm": result["d_tag_mm"],
                            "residual_rms_mm": result["residual_rms_mm"],
                            "anchors_input": result["anchors_input"],
                        }
                    )
    return {"session_rows": session_rows, "frame_diag_rows": frame_diag_rows}


def summarize_static_sessions(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    df = pd.DataFrame(rows)
    out: list[dict] = []
    for (case_id, layout_name, case_kind, fixed_tag_delay), g in df.groupby(["case_id", "layout_name", "case_kind", "fixed_tag_delay_mm"], dropna=False):
        err3 = g["err_3d_mm"].to_numpy(dtype=float)
        horiz = g["err_horizontal_mm"].to_numpy(dtype=float)
        vert = g["err_vertical_mm"].to_numpy(dtype=float)
        dtag_med = g["d_tag_frame_median_mm"].to_numpy(dtype=float)
        row = {
            "case_id": case_id,
            "layout_name": layout_name,
            "case_kind": case_kind,
            "fixed_tag_delay_mm": fixed_tag_delay,
            "n_sessions": int(len(g)),
            "err_3d_median_mm": percentile(err3, 50),
            "err_3d_p95_mm": percentile(err3, 95),
            "err_3d_rmse_mm": rmse(err3),
            "err_horizontal_median_mm": percentile(horiz, 50),
            "err_horizontal_p95_mm": percentile(horiz, 95),
            "err_vertical_median_mm": percentile(vert, 50),
            "err_vertical_p95_mm": percentile(vert, 95),
            "d_tag_session_median_p50_mm": percentile(dtag_med, 50),
            "d_tag_session_median_p25_mm": percentile(dtag_med, 25),
            "d_tag_session_median_p75_mm": percentile(dtag_med, 75),
            "d_tag_session_median_iqr_mm": iqr(dtag_med),
            "d_tag_frame_iqr_session_median_mm": percentile(g["d_tag_frame_iqr_mm"].to_numpy(dtype=float), 50),
            "residual_rms_session_median_mm": percentile(g["residual_rms_median_mm"].to_numpy(dtype=float), 50),
            "pct_solved_ge8_session_median": percentile(g["pct_solved_ge8"].to_numpy(dtype=float), 50),
        }
        out.append(row)
    return sorted(out, key=lambda r: (str(r["case_id"])))


def plot_fixed_sweep(rows: list[dict], out_png: Path) -> None:
    df = pd.DataFrame(rows)
    sub = df[df["case_kind"] == "fixed"].copy()
    if sub.empty:
        return
    fig, ax = plt.subplots(figsize=(7.0, 4.5), constrained_layout=True)
    for layout_name, g in sub.groupby("layout_name"):
        g = g.sort_values("fixed_tag_delay_mm")
        ax.plot(g["fixed_tag_delay_mm"], g["err_3d_median_mm"], marker="o", label=layout_name)
    ax.axvline(94.62, color="#666666", linestyle="--", linewidth=1.0, label="oracle mean 94.6 mm")
    ax.set_xlabel("fixed tag_delay_mm")
    ax.set_ylabel("static T4 mean 3D median error (mm)")
    ax.set_title("Fixed tag-delay sweep")
    ax.grid(True, alpha=0.3)
    ax.legend()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=180)
    plt.close(fig)


def solve_roto_joint_track_worker(job: dict) -> dict:
    layout = load_layout_json(Path(job["layout_path"]), Path(job["sigma_path"]))
    frames = read_tr_all_frames(Path(job["tr_all_path"]), tags={str(job["tag"])}, min_anchors=4)
    rows = []
    for frame in sorted(frames, key=lambda f: (float(f.host_elapsed_s), int(f.sweep))):
        result = joint_solve_frame(layout, frame, dtag_init_mm=0.0)
        if result is None:
            continue
        rows.append(
            (
                float(frame.host_elapsed_s),
                result["x_mm"],
                result["y_mm"],
                result["z_mm"],
                result["d_tag_mm"],
                result["residual_rms_mm"],
                result["anchors_input"],
                result["anchors_used"],
            )
        )
    arr = np.asarray(rows, dtype=float)
    if arr.size == 0:
        arr = np.empty((0, 8), dtype=float)
    return {
        "layout": str(job["layout_name"]),
        "capture_id": str(job["capture_id"]),
        "tag": str(job["tag"]),
        "source_tr_all": str(job["tr_all_path"]),
        "time_s": arr[:, 0],
        "xyz_autopos_mm": arr[:, 1:4],
        "d_tag_mm": arr[:, 4],
        "residual_rms_mm": arr[:, 5],
        "anchors_input": arr[:, 6],
        "anchors_used": arr[:, 7],
    }


def run_roto_joint_spot(layouts: list[dict], workers: int) -> tuple[list[dict], list[dict], list[dict]]:
    roto_mod = load_module(ROTO_ABS_SCRIPT, "audit_phase1d_roto_absolute")
    anchor_truth, _static_truth, _static_meta, _corr = load_corrected_static_truth(OPTI_ROOT, ANCHORS, ["ID01", "ID02", "ID03", "ID04", "ID05"])
    truth_coords = np.vstack([anchor_truth[a] for a in ANCHORS])
    transforms: dict[str, Any] = {}
    layout_anchor_rms: dict[str, float] = {}
    for item in layouts:
        labels, coords, _delays, _td = load_layout_coords(Path(item["layout_path"]))
        src = np.vstack([coords[labels.index(a)] for a in ANCHORS])
        fit = roto_mod.fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=False)
        transforms[item["layout_name"]] = fit
        diff = fit.aligned_anchors - truth_coords
        layout_anchor_rms[item["layout_name"]] = float(math.sqrt(np.mean(np.sum(diff * diff, axis=1))))

    tr_all_by_capture = roto_mod.discover_roto_capture_files(CAPTURES_ROOT)
    capture_ids = sorted(tr_all_by_capture)
    opti_by_capture = {
        cid: roto_mod.parse_trc_trajectories(OPTI_ROOT / f"{cid}.trc", roto_mod.OPTITRACK_MARKERS)
        for cid in capture_ids
    }
    mapping = dict(roto_mod.DEFAULT_MAPPING)

    jobs = []
    for item in layouts:
        for capture_id, tr_path in tr_all_by_capture.items():
            for tag in ROTO_TAGS:
                jobs.append(
                    {
                        "layout_name": item["layout_name"],
                        "layout_path": str(item["layout_path"]),
                        "sigma_path": str(item["sigma_path"]),
                        "capture_id": capture_id,
                        "tag": tag,
                        "tr_all_path": str(tr_path),
                    }
                )
    solved: dict[tuple[str, str, str], Any] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(solve_roto_joint_track_worker, job) for job in jobs]
        done = 0
        for fut in as_completed(futures):
            raw = fut.result()
            layout_name = raw["layout"]
            fit = transforms[layout_name]
            xyz_autopos = np.asarray(raw["xyz_autopos_mm"], dtype=float)
            xyz_opti = roto_mod.apply_transform(xyz_autopos, fit) if xyz_autopos.size else np.empty((0, 3), dtype=float)
            solved[(layout_name, raw["capture_id"], raw["tag"])] = roto_mod.SolvedTrack(
                layout=layout_name,
                tag_method="T4_joint_dtag",
                capture_id=raw["capture_id"],
                tag=raw["tag"],
                time_s=np.asarray(raw["time_s"], dtype=float),
                xyz_autopos_mm=xyz_autopos,
                xyz_opti_frame_mm=xyz_opti,
                residual_rms_mm=np.asarray(raw["residual_rms_mm"], dtype=float),
                anchors_input=np.asarray(raw["anchors_input"], dtype=float),
                anchors_used=np.asarray(raw["anchors_used"], dtype=float),
                source_tr_all=raw["source_tr_all"],
            )
            solved[(layout_name, raw["capture_id"], raw["tag"])].d_tag_mm = np.asarray(raw["d_tag_mm"], dtype=float)  # type: ignore[attr-defined]
            done += 1
            if done == 1 or done % 10 == 0 or done == len(jobs):
                print(f"[phase1d-roto] solved {done}/{len(jobs)} joint-dtag tracks", flush=True)

    track_rows: list[dict] = []
    offset_rows: list[dict] = []
    dtag_rows: list[dict] = []
    for item in layouts:
        layout_name = item["layout_name"]
        primary_by_capture: dict[str, dict[str, Any]] = {}
        for cid in capture_ids:
            primary_by_capture[cid] = {
                tag: solved[(layout_name, cid, tag)]
                for tag in ROTO_TAGS
                if (layout_name, cid, tag) in solved
            }
        beta_by_capture: dict[str, float] = {}
        for cid in capture_ids:
            offset, _cand = roto_mod.estimate_capture_offset(
                cid,
                primary_by_capture[cid],
                opti_by_capture[cid],
                mapping,
                coarse_step_s=0.05,
                refine_step_s=0.005,
                min_points=500,
            )
            offset["layout_name"] = layout_name
            offset_rows.append(offset)
            if offset.get("status") == "ok" and math.isfinite(float(offset.get("beta_s", float("nan")))):
                beta_by_capture[cid] = float(offset["beta_s"])
        for cid in capture_ids:
            if cid not in beta_by_capture:
                continue
            beta = beta_by_capture[cid]
            for tag in ROTO_TAGS:
                key = (layout_name, cid, tag)
                if key not in solved:
                    continue
                marker = mapping[tag]
                row, _samples = roto_mod.evaluate_track(
                    solved[key],
                    opti_by_capture[cid][marker],
                    beta_s=beta,
                    layout_det=float(transforms[layout_name].det),
                    layout_anchor_rms_mm=layout_anchor_rms[layout_name],
                    include_samples=False,
                )
                row["opti_marker"] = marker
                row["time_offset_source"] = "capture-level joint-dtag trajectory fit for same layout"
                dvals = getattr(solved[key], "d_tag_mm", np.empty(0))
                row["d_tag_track_median_mm"] = percentile(dvals, 50)
                row["d_tag_track_iqr_mm"] = iqr(dvals)
                track_rows.append(row)
                dtag_rows.append(
                    {
                        "layout_name": layout_name,
                        "capture_id": cid,
                        "tag": tag,
                        "d_tag_track_median_mm": percentile(dvals, 50),
                        "d_tag_track_p25_mm": percentile(dvals, 25),
                        "d_tag_track_p75_mm": percentile(dvals, 75),
                        "d_tag_track_iqr_mm": iqr(dvals),
                        "d_tag_track_p5_mm": percentile(dvals, 5),
                        "d_tag_track_p95_mm": percentile(dvals, 95),
                    }
                )
    summary_rows = []
    for layout_name in [item["layout_name"] for item in layouts]:
        sub = [r for r in track_rows if r.get("layout") == layout_name and r.get("status") == "ok"]
        if not sub:
            continue
        summary_rows.append(
            {
                "layout_name": layout_name,
                "tag_method": "T4_joint_dtag",
                "tracks_ok": len(sub),
                "captures_ok": len({r["capture_id"] for r in sub}),
                "n_overlap_total": int(sum(int(r["n_overlap"]) for r in sub)),
                "err3d_p50_track_median_mm": percentile([r["err3d_p50_mm"] for r in sub], 50),
                "err3d_p95_track_median_mm": percentile([r["err3d_p95_mm"] for r in sub], 50),
                "err3d_rmse_track_median_mm": percentile([r["err3d_rmse_mm"] for r in sub], 50),
                "err_horizontal_xz_p50_track_median_mm": percentile([r["err_horizontal_xz_p50_mm"] for r in sub], 50),
                "err_vertical_y_p50_track_median_mm": percentile([r["err_vertical_y_p50_mm"] for r in sub], 50),
                "d_tag_track_median_p50_mm": percentile([r["d_tag_track_median_mm"] for r in sub], 50),
                "d_tag_track_median_iqr_mm": iqr([r["d_tag_track_median_mm"] for r in sub]),
            }
        )
    return summary_rows, track_rows, offset_rows + dtag_rows


def build_closure_table() -> tuple[list[dict], dict]:
    oracle = pd.read_csv(PHASE1C_ORACLE_ANCHORS)
    common = pd.read_csv(PHASE1C_COMMON_ANCHORS)
    labels, _coords, prod_delay, _td = load_layout_coords(CURRENT_LAYOUT)
    phase1 = json.loads(PHASE1_SUMMARY.read_text(encoding="utf-8"))
    phase1c = json.loads(PHASE1C_SUMMARY.read_text(encoding="utf-8"))
    deltas = phase1["item2"]["unconstrained_deltas_mm"]
    rows = []
    for i, anchor in enumerate(ANCHORS):
        o = oracle[oracle["anchor"] == anchor].iloc[0]
        c = common[common["anchor"] == anchor].iloc[0]
        rows.append(
            {
                "anchor": anchor,
                "oracle_d_i_mm": float(o["oracle_d_i_mm"]),
                "phase1_unconstrained_delta_i_mm": float(deltas[anchor]),
                "phase1c_common_mode_c_plus_e_i_mm": float(c["d_i_c_plus_e_i_mm"]),
                "production_v4io_fitted_delay_mm": float(prod_delay[labels.index(anchor)]),
            }
        )
    mean_oracle = float(phase1c["item1_oracle"]["mean_oracle_d_mm"])
    raw_per_link = 2.0 * mean_oracle
    prod_absorb = float(np.sum(prod_delay) / 4.0)
    residual = raw_per_link - prod_absorb
    measured = float(phase1["item1"]["mean_delta_mm"])
    closure = {
        "oracle_mean_d_i_mm": mean_oracle,
        "raw_per_link_bias_2x_oracle_mean_mm": raw_per_link,
        "production_delay_sum_mm": float(np.sum(prod_delay)),
        "production_per_link_absorbed_sum_over_4_mm": prod_absorb,
        "raw_minus_production_absorbed_mm": residual,
        "measured_phase1_mean_pair_delta_mm": measured,
        "closure_difference_mm": residual - measured,
        "oracle_A_mm": float(oracle[oracle["anchor"] == "A"]["oracle_d_i_mm"].iloc[0]),
        "phase1_delta_A_mm": float(deltas["A"]),
        "oracle_A_minus_delta_A_mm": float(oracle[oracle["anchor"] == "A"]["oracle_d_i_mm"].iloc[0] - float(deltas["A"])),
    }
    return rows, closure


def main() -> int:
    started = time.perf_counter()
    TABLES.mkdir(parents=True, exist_ok=True)
    FIGS.mkdir(parents=True, exist_ok=True)
    layouts = [
        {
            "layout_name": "production_v4io",
            "layout_path": CURRENT_LAYOUT,
            "sigma_path": CURRENT_SIGMA,
        },
        {
            "layout_name": "phase1c_common_mode",
            "layout_path": PHASE1C_LAYOUT,
            "sigma_path": PHASE1C_SIGMA,
        },
    ]

    static_jobs = []
    for item in layouts:
        static_jobs.append({**item, "case_kind": "joint", "point_estimator": "mean"})
        for dtag in FIXED_TAG_DELAYS:
            static_jobs.append({**item, "case_kind": "fixed", "fixed_tag_delay_mm": float(dtag), "point_estimator": "mean"})
    print(f"[phase1d-static] running {len(static_jobs)} static cases with {MAX_WORKERS} workers", flush=True)
    static_session_rows: list[dict] = []
    static_frame_diag_rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(evaluate_static_case, job) for job in static_jobs]
        done = 0
        for fut in as_completed(futures):
            raw = fut.result()
            static_session_rows.extend(raw["session_rows"])
            static_frame_diag_rows.extend(raw["frame_diag_rows"])
            done += 1
            print(f"[phase1d-static] completed {done}/{len(static_jobs)} cases", flush=True)
    static_summary = summarize_static_sessions(static_session_rows)
    write_csv(TABLES / "item1_item2_static_per_session.csv", static_session_rows)
    write_csv(TABLES / "item1_joint_dtag_frame_diagnostics_sampled.csv", static_frame_diag_rows)
    write_csv(TABLES / "item1_item2_static_summary.csv", static_summary)
    plot_fixed_sweep(static_summary, FIGS / "item2_fixed_tag_delay_sweep.png")

    closure_rows, closure = build_closure_table()
    write_csv(TABLES / "item3_consolidated_delay_table.csv", closure_rows)
    write_csv(TABLES / "item3_closure_arithmetic.csv", [closure])

    print(f"[phase1d-roto] running joint-dtag RotoArm spot check with {MAX_WORKERS} workers", flush=True)
    roto_summary, roto_track_rows, roto_aux_rows = run_roto_joint_spot(layouts, MAX_WORKERS)
    write_csv(TABLES / "item4_roto_joint_dtag_summary.csv", roto_summary)
    write_csv(TABLES / "item4_roto_joint_dtag_per_track.csv", roto_track_rows)
    write_csv(TABLES / "item4_roto_joint_dtag_offsets_and_delay.csv", roto_aux_rows)

    baseline_static = pd.read_csv(CURRENT_STATIC_SUMMARY).iloc[0].to_dict()
    baseline_roto_rows = pd.read_csv(CURRENT_ROTO_SUMMARY)
    baseline_roto = baseline_roto_rows[(baseline_roto_rows["layout"] == "v4-io") & (baseline_roto_rows["tag_method"] == "T4")].iloc[0].to_dict()
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_s": time.perf_counter() - started,
        "script": str(THIS),
        "paths": {
            "out": str(OUT),
            "tables": str(TABLES),
            "figs": str(FIGS),
            "production_layout": str(CURRENT_LAYOUT),
            "phase1c_common_mode_layout": str(PHASE1C_LAYOUT),
        },
        "baselines": {
            "static_production_t4_mean": baseline_static,
            "roto_production_v4io_t4": baseline_roto,
        },
        "item1_item2_static_summary": static_summary,
        "item3_closure": closure,
        "item4_roto_summary": roto_summary,
    }
    dump_json(TABLES / "audit_phase1d_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
