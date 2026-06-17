#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from multiprocessing import get_context
from pathlib import Path
from typing import Any

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

import numpy as np
import pandas as pd
import psutil


ANCHORS = list("ABCDEFGH")
STATIC_TAG = "BSF66F"
ROTO_TAGS = ["BS2DCE", "BSDC91"]
ROTO_MARKERS = ["WandBantenna", "WandCantenna"]
PRIMARY_IDS = ["ID01", "ID02", "ID03", "ID04", "ID05"]
UNIFIED_COLUMNS = [
    "run_id",
    "phase",
    "layout_source",
    "correction_source",
    "correction_detail",
    "tag_delay_mode",
    "tag_delay_value_mm",
    "tag_solver",
    "n_positions",
    "n_frames",
    "median_3d_mm",
    "p75_3d_mm",
    "p95_3d_mm",
    "rmse_3d_mm",
    "median_horiz_mm",
    "rmse_horiz_mm",
    "median_vert_mm",
    "rmse_vert_mm",
    "signed_vertical_slope_mm_per_m",
    "signed_vertical_slope_r2",
    "fail_rate",
    "notes",
]


THIS = Path(__file__).resolve()
ANALYSIS = THIS.parents[2]
BASE = THIS.parents[4]
REPO_ROOT = THIS.parents[6]
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"
FULL_ROOT = ANALYSIS / "FULL"
FULL_4WAY = ANALYSIS / "FULL_4way_comparison"
ABLATION_SCRIPT = FULL_4WAY / "scripts/run_static_layout_ablation.py"
ROTOARM_SCRIPT = FULL_4WAY / "scripts/run_rotoarm_scale_delay_nlos.py"
LAYOUT_BASE = BASE / "solver/outputs/v1_to_v4_io_field_check"
V5_LAYOUT = LAYOUT_BASE / "v5-commonmode/layout.json"
SIGMA_PATH = LAYOUT_BASE / "tables/anchor_sigma.json"
PAIR_QUALITY = LAYOUT_BASE / "tables/pair_quality_solve.csv"
STATIC_TABLE = LAYOUT_BASE / "tables/static_all_captures.csv"
CAPTURES_ROOT = BASE / "captures/erlangen_20260528_optitrack"
OPTI_ROOT = BASE / "opti_captures/full"
OFFSETS_PATH = FULL_ROOT / "roto_absolute/tables/roto_time_offsets_v4io_T4.csv"
MAPPING_PATH = FULL_ROOT / "roto_absolute/tables/roto_wand_mapping_decision.csv"

sys.path.insert(0, str(SOLVER_ROOT))
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json  # noqa: E402
from biospur_tag_positioning_offline_solver.models import SolverConfig  # noqa: E402
from biospur_tag_positioning_offline_solver.trajectory import solve_capture_trajectory  # noqa: E402


@dataclass(frozen=True)
class Fit:
    rotation: np.ndarray
    translation: np.ndarray
    scale: float
    det: float
    aligned: np.ndarray


@dataclass(frozen=True)
class OptiTrackTrajectory:
    time_s: np.ndarray
    xyz_mm: np.ndarray


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
        raise FileNotFoundError(f"missing required {label}: {path}")
    return path


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(fieldnames or [])
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_md_table(lines: list[str], rows: list[dict[str, Any]], cols: list[str], max_rows: int | None = None) -> None:
    lines.append("| " + " | ".join(cols) + " |\n")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |\n")
    for row in rows[: max_rows or len(rows)]:
        vals = []
        for col in cols:
            val = row.get(col, "")
            if isinstance(val, float):
                vals.append("nan" if not math.isfinite(val) else f"{val:.3f}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |\n")
    lines.append("\n")


def finite(values: Any) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    return arr[np.isfinite(arr)]


def percentile(values: Any, pct: float) -> float:
    arr = finite(values)
    return float(np.percentile(arr, pct)) if arr.size else float("nan")


def rmse(values: Any) -> float:
    arr = finite(values)
    return float(math.sqrt(np.mean(arr * arr))) if arr.size else float("nan")


def regression_slope_mm_per_m(x_mm: Any, y_mm: Any) -> tuple[float, float]:
    x = np.asarray(x_mm, dtype=float)
    y = np.asarray(y_mm, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    xx = x[mask]
    yy = y[mask]
    if xx.size < 3 or float(np.nanstd(xx)) <= 1e-12:
        return float("nan"), float("nan")
    slope, intercept = np.polyfit(xx, yy, 1)
    pred = slope * xx + intercept
    ss_res = float(np.sum((yy - pred) ** 2))
    ss_tot = float(np.sum((yy - np.mean(yy)) ** 2))
    return float(slope * 1000.0), float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_reflection: bool, allow_scale: bool) -> Fit:
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3:
        raise ValueError(f"bad fit shape {src.shape} vs {dst.shape}")
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, svals, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    scale = 1.0
    if allow_scale:
        denom = float(np.sum(x * x))
        scale = float(np.sum(svals * d) / denom) if denom > 0.0 else 1.0
    t = dst_c - scale * src_c @ r
    aligned = scale * src @ r + t
    return Fit(rotation=r, translation=t, scale=scale, det=float(np.linalg.det(r)), aligned=aligned)


def fit_with_fixed_scale(src: np.ndarray, dst: np.ndarray, scale: float) -> Fit:
    base = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    t = dst_c - float(scale) * src_c @ base.rotation
    aligned = float(scale) * src @ base.rotation + t
    return Fit(rotation=base.rotation, translation=t, scale=float(scale), det=base.det, aligned=aligned)


def apply_fit(points: np.ndarray, fit: Fit) -> np.ndarray:
    pts = np.asarray(points, dtype=float)
    return fit.scale * pts @ fit.rotation + fit.translation


def rotation_to_quaternion(r: np.ndarray) -> tuple[float, float, float, float]:
    tr = float(np.trace(r))
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        return (0.25 * s, (r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s)
    i = int(np.argmax(np.diag(r)))
    if i == 0:
        s = math.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2.0
        return ((r[2, 1] - r[1, 2]) / s, 0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s)
    if i == 1:
        s = math.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2.0
        return ((r[0, 2] - r[2, 0]) / s, (r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s)
    s = math.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2.0
    return ((r[1, 0] - r[0, 1]) / s, (r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s)


def load_layout_raw(path: Path) -> tuple[list[str], np.ndarray, dict[int, float], float, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    labels: list[str] = []
    coords: list[list[float]] = []
    delays: dict[int, float] = {}
    for item in data["anchors"]:
        aid_raw = item.get("id", item.get("label"))
        aid = ANCHORS.index(aid_raw.upper()) if isinstance(aid_raw, str) and aid_raw.upper() in ANCHORS else int(aid_raw)
        labels.append(str(item.get("label") or ANCHORS[aid]))
        coords.append([float(item["x_mm"]), float(item["y_mm"]), float(item["z_mm"])])
        delays[aid] = float(item.get("d_anchor_mm") or 0.0)
    for aid in range(8):
        delays.setdefault(aid, 0.0)
    return labels, np.asarray(coords, dtype=float), delays, float(data.get("tag_delay_mm") or 0.0), data


def write_layout_json(path: Path, *, name: str, coords: np.ndarray, delays: dict[int, float], tag_delay_mm: float = 0.0) -> None:
    obj = {
        "version": name,
        "label": name,
        "anchor_ids": list(range(8)),
        "anchors": [
            {
                "id": int(aid),
                "label": ANCHORS[aid],
                "x_mm": float(coords[aid, 0]),
                "y_mm": float(coords[aid, 1]),
                "z_mm": float(coords[aid, 2]),
                "d_anchor_mm": float(delays.get(aid, 0.0)),
            }
            for aid in range(8)
        ],
        "tag_delay_mm": float(tag_delay_mm),
        "extra": {"generated_by": str(THIS), "generated_utc": datetime.now().isoformat(timespec="seconds")},
    }
    write_json(path, obj)


def find_v4_layout() -> Path:
    candidates = sorted(
        p
        for p in LAYOUT_BASE.iterdir()
        if p.is_dir() and (p / "layout.json").exists() and "commonmode" not in p.name.lower() and p.name.startswith("v4")
    )
    if len(candidates) != 1:
        tried = [str(p) for p in sorted(LAYOUT_BASE.glob("*/layout.json"))]
        raise RuntimeError(f"cannot uniquely locate V4-io sibling without commonmode under {LAYOUT_BASE}; candidates={candidates}; tried={tried}")
    return candidates[0] / "layout.json"


def discover_roto_files() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in sorted(CAPTURES_ROOT.glob("roto_R[0-9][0-9]*/tag_capture*/tr_all.csv")):
        if "Static-middle-test" in path.parents[1].name:
            continue
        m = re.search(r"roto_(R\d\d)_", path.parents[1].name)
        if m:
            out[m.group(1)] = path
    return dict(sorted(out.items()))


def static_id_from_path(path: Path) -> str:
    for parent in path.parents:
        if parent.name.startswith("static_ID"):
            return parent.name.split("_")[1]
    raise ValueError(f"cannot parse static ID from {path}")


def read_mapping(path: Path) -> dict[str, str]:
    best: dict[str, str] | None = None
    best_score = float("inf")
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("capture_id") != "ALL":
                continue
            score = float(row.get("score_median_3d_mm") or "inf")
            if score < best_score:
                best_score = score
                best = {"BS2DCE": str(row["BS2DCE_marker"]), "BSDC91": str(row["BSDC91_marker"])}
    if best is None:
        raise RuntimeError(f"missing ALL mapping decision in {path}")
    return best


def read_offsets(path: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("status") == "ok":
                beta = float(row["beta_s"])
                if math.isfinite(beta):
                    out[str(row["capture_id"])] = beta
    if not out:
        raise RuntimeError(f"no ok offsets in {path}")
    return dict(sorted(out.items()))


def parse_trc_trajectories(path: Path, markers: list[str]) -> dict[str, OptiTrackTrajectory]:
    with path.open("r", errors="replace", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    if len(rows) < 6:
        raise ValueError(f"TRC too short: {path}")
    marker_row = [x.strip() for x in rows[3][2:] if x.strip()]
    marker_to_index = {name: i for i, name in enumerate(marker_row)}
    missing = [m for m in markers if m not in marker_to_index]
    if missing:
        raise KeyError(f"{missing} missing in {path}")
    data = []
    for row in rows[5:]:
        if not row or not row[0].strip():
            continue
        vals = []
        for field in row:
            try:
                vals.append(float(field.strip()) if field.strip() else float("nan"))
            except ValueError:
                vals.append(float("nan"))
        data.append(vals)
    arr = np.full((len(data), max(len(r) for r in data)), np.nan)
    for i, row in enumerate(data):
        arr[i, : len(row)] = row
    time_s = arr[:, 1]
    out: dict[str, OptiTrackTrajectory] = {}
    for marker in markers:
        start = 2 + marker_to_index[marker] * 3
        xyz = arr[:, start : start + 3]
        good = np.isfinite(time_s) & np.isfinite(xyz).all(axis=1)
        out[marker] = OptiTrackTrajectory(time_s=time_s[good], xyz_mm=xyz[good])
    return out


def interpolate_opti(traj: OptiTrackTrajectory, query_time_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = traj.time_s
    q = np.asarray(query_time_s, dtype=float)
    good = np.isfinite(q) & (q >= t[0]) & (q <= t[-1])
    out = np.full((q.shape[0], 3), np.nan)
    for axis in range(3):
        out[good, axis] = np.interp(q[good], t, traj.xyz_mm[:, axis])
    return out, good


def fit_circle_3d(points: np.ndarray) -> dict[str, Any]:
    pts = np.asarray(points, dtype=float)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if pts.shape[0] < 30:
        return {"status": "insufficient", "n": int(pts.shape[0])}
    center0 = np.mean(pts, axis=0)
    _u, _s, vh = np.linalg.svd(pts - center0, full_matrices=False)
    e1, e2, normal = vh[0], vh[1], vh[-1]
    uv = np.column_stack([(pts - center0) @ e1, (pts - center0) @ e2])
    x, y = uv[:, 0], uv[:, 1]
    a = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    b = x * x + y * y
    cx, cy, c = np.linalg.lstsq(a, b, rcond=None)[0]
    radius = math.sqrt(max(0.0, float(c + cx * cx + cy * cy)))
    center3 = center0 + cx * e1 + cy * e2
    radial = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - radius
    out_of_plane = (pts - center0) @ normal
    return {
        "status": "ok",
        "n": int(pts.shape[0]),
        "radius_mm": float(radius),
        "center_x_mm": float(center3[0]),
        "center_y_mm": float(center3[1]),
        "center_z_mm": float(center3[2]),
        "normal_x": float(normal[0]),
        "normal_y": float(normal[1]),
        "normal_z": float(normal[2]),
        "in_plane_radial_rms_mm": rmse(radial),
        "in_plane_radial_p95_abs_mm": percentile(np.abs(radial), 95),
        "out_of_plane_rms_mm": rmse(out_of_plane),
        "out_of_plane_p95_abs_mm": percentile(np.abs(out_of_plane), 95),
    }


def aggregate_static_rows(rows: list[dict[str, Any]], *, expected_positions: int = 24) -> dict[str, Any]:
    df = pd.DataFrame(rows)
    if df.empty:
        return {
            "n_positions": 0,
            "n_frames": 0,
            "median_3d_mm": float("nan"),
            "p75_3d_mm": float("nan"),
            "p95_3d_mm": float("nan"),
            "rmse_3d_mm": float("nan"),
            "median_horiz_mm": float("nan"),
            "rmse_horiz_mm": float("nan"),
            "median_vert_mm": float("nan"),
            "rmse_vert_mm": float("nan"),
            "signed_vertical_slope_mm_per_m": float("nan"),
            "signed_vertical_slope_r2": float("nan"),
            "fail_rate": 1.0,
            "residual_rms_median_mm": float("nan"),
            "d3_std_median_mm": float("nan"),
            "distance_to_centroid_slope_mm_per_m": float("nan"),
            "distance_to_centroid_slope_r2": float("nan"),
        }
    err = df["err_3d_mm"].to_numpy(dtype=float)
    horiz = df["err_horizontal_xz_mm"].to_numpy(dtype=float)
    vert = df["err_vertical_y_mm"].to_numpy(dtype=float)
    signed_y = df["err_y_vertical_mm"].to_numpy(dtype=float)
    truth_y = df["truth_y_vertical_mm"].to_numpy(dtype=float)
    slope, r2 = regression_slope_mm_per_m(truth_y, signed_y)
    if "distance_to_array_centroid_mm" in df:
        ds, dr2 = regression_slope_mm_per_m(df["distance_to_array_centroid_mm"].to_numpy(dtype=float), err)
    else:
        ds, dr2 = float("nan"), float("nan")
    return {
        "n_positions": int(len(df)),
        "n_frames": int(df["frames_solved"].sum()) if "frames_solved" in df else 0,
        "frames_input_total": int(df["frames_input"].sum()) if "frames_input" in df else 0,
        "median_3d_mm": percentile(err, 50),
        "p75_3d_mm": percentile(err, 75),
        "p95_3d_mm": percentile(err, 95),
        "rmse_3d_mm": rmse(err),
        "median_horiz_mm": percentile(horiz, 50),
        "rmse_horiz_mm": rmse(horiz),
        "median_vert_mm": percentile(vert, 50),
        "rmse_vert_mm": rmse(vert),
        "signed_vertical_slope_mm_per_m": slope,
        "signed_vertical_slope_r2": r2,
        "fail_rate": float(max(0, expected_positions - len(df)) / expected_positions),
        "residual_rms_median_mm": percentile(df["residual_rms_median_mm"], 50) if "residual_rms_median_mm" in df else float("nan"),
        "d3_std_median_mm": percentile(df["d3_std_mm"], 50) if "d3_std_mm" in df else float("nan"),
        "distance_to_centroid_slope_mm_per_m": ds,
        "distance_to_centroid_slope_r2": dr2,
    }


def unified_row(
    *,
    run_id: str,
    phase: str,
    layout_source: str,
    correction_source: str,
    correction_detail: str,
    tag_delay_mode: str,
    tag_delay_value_mm: float,
    tag_solver: str,
    summary: dict[str, Any],
    notes: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "phase": phase,
        "layout_source": layout_source,
        "correction_source": correction_source,
        "correction_detail": correction_detail,
        "tag_delay_mode": tag_delay_mode,
        "tag_delay_value_mm": float(tag_delay_value_mm),
        "tag_solver": tag_solver,
        "n_positions": int(summary.get("n_positions", 0)),
        "n_frames": int(summary.get("n_frames", 0)),
        "median_3d_mm": summary.get("median_3d_mm", float("nan")),
        "p75_3d_mm": summary.get("p75_3d_mm", float("nan")),
        "p95_3d_mm": summary.get("p95_3d_mm", float("nan")),
        "rmse_3d_mm": summary.get("rmse_3d_mm", float("nan")),
        "median_horiz_mm": summary.get("median_horiz_mm", float("nan")),
        "rmse_horiz_mm": summary.get("rmse_horiz_mm", float("nan")),
        "median_vert_mm": summary.get("median_vert_mm", float("nan")),
        "rmse_vert_mm": summary.get("rmse_vert_mm", float("nan")),
        "signed_vertical_slope_mm_per_m": summary.get("signed_vertical_slope_mm_per_m", float("nan")),
        "signed_vertical_slope_r2": summary.get("signed_vertical_slope_r2", float("nan")),
        "fail_rate": summary.get("fail_rate", float("nan")),
        "notes": notes,
    }


def static_cell_worker(job: dict[str, Any]) -> dict[str, Any]:
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    ab = load_module(Path(job["ablation_script"]), f"static_ablation_worker_{os.getpid()}")
    coords = np.asarray(job["coords"], dtype=float)
    delays = {int(k): float(v) for k, v in job["delays"].items()}
    layout = ab.build_layout(
        name=job["layout_name"],
        labels=ANCHORS,
        coords_opti_frame=coords,
        delays=delays,
        tag_delay_mm=0.0,
        sigma_by_id={int(k): float(v) for k, v in job["sigma_by_id"].items()},
        metadata=job.get("metadata", {}),
    )
    solver = ab.TagPositionSolver(
        layout,
        ab.SolverConfig(method=str(job.get("method", "T4"))),
        tag_delay_by_tag={STATIC_TAG: float(job["d_tag_mm"])},
    )
    tag_truth = {k: np.asarray(v, dtype=float) for k, v in job["tag_truth"].items()}
    anchor_centroid = np.asarray(job["anchor_centroid"], dtype=float)
    rows: list[dict[str, Any]] = []
    rho_rows: list[dict[str, Any]] = []
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
        if row is not None:
            truth = np.asarray([row["truth_x_mm"], row["truth_y_vertical_mm"], row["truth_z_mm"]], dtype=float)
            row["distance_to_array_centroid_mm"] = float(np.linalg.norm(truth - anchor_centroid))
            row["tag_delay_mode"] = job.get("tag_delay_mode", "")
            row["tag_delay_value_mm"] = float(job["d_tag_mm"])
            rows.append(row)
        if job.get("need_rho"):
            frames = ab.read_tr_all_frames(path, tags={STATIC_TAG}, min_anchors=4)
            frames = ab.filter_frames(frames, set(range(8)))
            local_solver = ab.TagPositionSolver(
                layout,
                ab.SolverConfig(method=str(job.get("method", "T4"))),
                tag_delay_by_tag={STATIC_TAG: float(job["d_tag_mm"])},
            )
            frame_by_sweep = {int(f.sweep): f for f in frames}
            for frame in frames:
                result = local_solver.solve_frame(frame)
                if result is None or result.status != "ok":
                    continue
                p = np.asarray([result.x_mm, result.y_mm, result.z_mm], dtype=float)
                for obs in frame.observations:
                    aid = int(obs.anchor_id)
                    anchor = layout.anchors.get(aid)
                    if anchor is None:
                        continue
                    a = np.asarray([anchor.x_mm, anchor.y_mm, anchor.z_mm], dtype=float)
                    predicted = float(np.linalg.norm(p - a) + anchor.d_anchor_mm + float(job["d_tag_mm"]))
                    rho_rows.append(
                        {
                            "dataset": "static",
                            "layout": job["layout_source"],
                            "d_tag_mm": float(job["d_tag_mm"]),
                            "method": str(job.get("method", "T4")),
                            "capture_id": "",
                            "static_id": static_id_from_path(path),
                            "tag": STATIC_TAG,
                            "sweep": int(frame.sweep),
                            "host_elapsed_s": float(frame.host_elapsed_s),
                            "host_epoch_s": float(frame.host_epoch_s),
                            "anchor_id": aid,
                            "anchor_label": ANCHORS[aid] if 0 <= aid < len(ANCHORS) else str(aid),
                            "range_measured_mm": float(obs.range_mm),
                            "range_predicted_mm": predicted,
                            "rho_mm": float(obs.range_mm - predicted),
                            "solver_used_anchor": bool(result.used_by_anchor.get(aid, False)),
                        }
                    )
            _ = frame_by_sweep
    summary = aggregate_static_rows(rows)
    return {
        "cell_id": job["cell_id"],
        "rows": rows,
        "summary": summary,
        "rho_rows": rho_rows,
        "metadata": job.get("metadata", {}),
        "tag_delay_mode": job.get("tag_delay_mode", ""),
        "d_tag_mm": float(job["d_tag_mm"]),
    }


def transfer_combo_worker(job: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    detail: list[dict[str, Any]] = []
    fixed: list[dict[str, Any]] = []
    for dtag in job["dtag_grid"]:
        sub = dict(job)
        sub.update({"d_tag_mm": float(dtag), "cell_id": f"{job['combo_id']}:sweep:{dtag:.3f}", "tag_delay_mode": "D_sweep_grid"})
        result = static_cell_worker(sub)
        summary = result["summary"]
        detail.append(
            {
                "layout_source": job["layout_source"],
                "correction_source": job["correction_source"],
                "correction_detail": job["correction_detail"],
                "d_tag_mm": float(dtag),
                **summary,
            }
        )
    best = min(detail, key=lambda r: (float(r["median_3d_mm"]), float(r["p95_3d_mm"])))
    for mode, dtag in job["fixed_dtags"].items():
        sub = dict(job)
        sub.update({"d_tag_mm": float(dtag), "cell_id": f"{job['combo_id']}:{mode}", "tag_delay_mode": mode})
        result = static_cell_worker(sub)
        fixed.append(
            {
                "layout_source": job["layout_source"],
                "correction_source": job["correction_source"],
                "correction_detail": job["correction_detail"],
                "tag_delay_mode": mode,
                "tag_delay_value_mm": float(dtag),
                **result["summary"],
            }
        )
    rows.extend(fixed)
    rows.append(
        {
            "layout_source": job["layout_source"],
            "correction_source": job["correction_source"],
            "correction_detail": job["correction_detail"],
            "tag_delay_mode": "D_sweep_opt",
            "tag_delay_value_mm": float(best["d_tag_mm"]),
            **{k: v for k, v in best.items() if k not in {"layout_source", "correction_source", "correction_detail", "d_tag_mm"}},
        }
    )
    return {"combo_id": job["combo_id"], "summary_rows": rows, "sweep_rows": detail}


def solve_roto_track_worker(job: dict[str, Any]) -> dict[str, Any]:
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    result = solve_capture_trajectory(
        Path(job["layout_path"]),
        Path(job["capture_path"]),
        method=str(job.get("method", "T4")),
        anchor_sigma_path=Path(job["sigma_path"]),
        tags={str(job["tag"])},
        tag_delay_by_tag={str(job["tag"]): float(job["d_tag_mm"])},
    )
    points = []
    rho_rows: list[dict[str, Any]] = []
    if job.get("need_rho"):
        layout = load_layout_json(job["layout_path"], job["sigma_path"])
        frames = read_tr_all_frames(job["capture_path"], tags={str(job["tag"])}, min_anchors=4)
        frame_by_key = {(f.tag.upper(), int(f.sweep)): f for f in frames}
    else:
        layout = None
        frame_by_key = {}
    for item in result.results:
        if item.status != "ok":
            continue
        points.append(
            {
                "time_s": float(item.host_elapsed_s),
                "host_epoch_s": float(item.host_epoch_s),
                "sweep": int(item.sweep),
                "x_mm": float(item.x_mm),
                "y_mm": float(item.y_mm),
                "z_mm": float(item.z_mm),
                "residual_rms_mm": float(item.residual_rms_mm),
                "anchors_input": int(item.anchors_input),
                "anchors_used": int(item.anchors_used),
            }
        )
        if job.get("need_rho") and layout is not None:
            frame = frame_by_key.get((str(job["tag"]).upper(), int(item.sweep)))
            if frame is None:
                continue
            p = np.asarray([item.x_mm, item.y_mm, item.z_mm], dtype=float)
            for obs in frame.observations:
                aid = int(obs.anchor_id)
                anchor = layout.anchors.get(aid)
                if anchor is None:
                    continue
                a = np.asarray([anchor.x_mm, anchor.y_mm, anchor.z_mm], dtype=float)
                predicted = float(np.linalg.norm(p - a) + anchor.d_anchor_mm + float(job["d_tag_mm"]))
                rho_rows.append(
                    {
                        "dataset": "dynamic",
                        "layout": job["layout_source"],
                        "d_tag_mm": float(job["d_tag_mm"]),
                        "method": str(job.get("method", "T4")),
                        "capture_id": str(job["capture_id"]),
                        "static_id": "",
                        "tag": str(job["tag"]),
                        "sweep": int(item.sweep),
                        "host_elapsed_s": float(item.host_elapsed_s),
                        "host_epoch_s": float(item.host_epoch_s),
                        "anchor_id": aid,
                        "anchor_label": ANCHORS[aid] if 0 <= aid < len(ANCHORS) else str(aid),
                        "range_measured_mm": float(obs.range_mm),
                        "range_predicted_mm": predicted,
                        "rho_mm": float(obs.range_mm - predicted),
                        "solver_used_anchor": bool(item.used_by_anchor.get(aid, False)),
                    }
                )
    return {
        "track_id": job["track_id"],
        "layout_source": job["layout_source"],
        "correction_source": job["correction_source"],
        "tag_delay_mode": job["tag_delay_mode"],
        "d_tag_mm": float(job["d_tag_mm"]),
        "capture_id": str(job["capture_id"]),
        "tag": str(job["tag"]),
        "frames_input": int(result.frames_input),
        "frames_solved": int(result.frames_solved),
        "points": points,
        "rho_rows": rho_rows,
    }


def evaluate_roto_tracks(
    solved_tracks: list[dict[str, Any]],
    *,
    fit: Fit | None,
    opti_by_capture: dict[str, dict[str, OptiTrackTrajectory]],
    mapping: dict[str, str],
    offsets: dict[str, float],
    alignment_note: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    track_rows: list[dict[str, Any]] = []
    circle_rows: list[dict[str, Any]] = []
    for tr in solved_tracks:
        pts = tr["points"]
        xyz = np.asarray([[p["x_mm"], p["y_mm"], p["z_mm"]] for p in pts], dtype=float)
        t = np.asarray([p["time_s"] for p in pts], dtype=float)
        aligned = apply_fit(xyz, fit) if fit is not None and xyz.size else xyz
        cid = tr["capture_id"]
        tag = tr["tag"]
        marker = mapping.get(tag, "")
        beta = offsets.get(cid, float("nan"))
        status = "ok"
        if not marker or cid not in opti_by_capture or not math.isfinite(beta):
            status = "missing_alignment_input"
            diff = np.empty((0, 3))
        else:
            truth, good = interpolate_opti(opti_by_capture[cid][marker], t + beta)
            finite_mask = good & np.isfinite(aligned).all(axis=1) & np.isfinite(truth).all(axis=1)
            diff = aligned[finite_mask] - truth[finite_mask]
        err3 = np.linalg.norm(diff, axis=1) if diff.size else np.empty(0)
        horiz = np.sqrt(diff[:, 0] ** 2 + diff[:, 2] ** 2) if diff.size else np.empty(0)
        vert = np.abs(diff[:, 1]) if diff.size else np.empty(0)
        track_rows.append(
            {
                "layout_source": tr["layout_source"],
                "correction_source": tr["correction_source"],
                "tag_delay_mode": tr["tag_delay_mode"],
                "d_tag_mm": tr["d_tag_mm"],
                "capture_id": cid,
                "tag": tag,
                "opti_marker": marker,
                "status": status if err3.size else "no_overlap",
                "n_overlap": int(err3.size),
                "frames_input": tr["frames_input"],
                "frames_solved": tr["frames_solved"],
                "beta_s": beta,
                "err3d_median_mm": percentile(err3, 50),
                "err3d_p75_mm": percentile(err3, 75),
                "err3d_p95_mm": percentile(err3, 95),
                "err3d_rmse_mm": rmse(err3),
                "median_horiz_mm": percentile(horiz, 50),
                "rmse_horiz_mm": rmse(horiz),
                "median_abs_vertical_y_mm": percentile(vert, 50),
                "rmse_vertical_y_mm": rmse(vert),
                "alignment": alignment_note,
            }
        )
        circle = fit_circle_3d(aligned)
        circle_rows.append(
            {
                "layout_source": tr["layout_source"],
                "correction_source": tr["correction_source"],
                "tag_delay_mode": tr["tag_delay_mode"],
                "d_tag_mm": tr["d_tag_mm"],
                "capture_id": cid,
                "tag": tag,
                "n_points": int(xyz.shape[0]),
                "alignment": alignment_note,
                **circle,
            }
        )
    summary_rows: list[dict[str, Any]] = []
    df = pd.DataFrame(track_rows)
    if not df.empty:
        for key, g in df.groupby(["layout_source", "correction_source", "tag_delay_mode", "d_tag_mm"], dropna=False):
            layout_source, correction_source, tag_delay_mode, dtag = key
            summary_rows.append(
                {
                    "layout_source": layout_source,
                    "correction_source": correction_source,
                    "tag_delay_mode": tag_delay_mode,
                    "d_tag_mm": float(dtag),
                    "n_positions": int(len(g)),
                    "n_frames": int(g["n_overlap"].sum()),
                    "median_3d_mm": percentile(g["err3d_median_mm"], 50),
                    "p75_3d_mm": percentile(g["err3d_median_mm"], 75),
                    "p95_3d_mm": percentile(g["err3d_p95_mm"], 50),
                    "rmse_3d_mm": percentile(g["err3d_rmse_mm"], 50),
                    "median_horiz_mm": percentile(g["median_horiz_mm"], 50),
                    "rmse_horiz_mm": percentile(g["rmse_horiz_mm"], 50),
                    "median_vert_mm": percentile(g["median_abs_vertical_y_mm"], 50),
                    "rmse_vert_mm": percentile(g["rmse_vertical_y_mm"], 50),
                    "signed_vertical_slope_mm_per_m": float("nan"),
                    "signed_vertical_slope_r2": float("nan"),
                    "fail_rate": float(np.mean(g["status"] != "ok")),
                }
            )
    return track_rows, circle_rows, summary_rows


def summarize_rho(rows: list[dict[str, Any]], dataset: str) -> list[dict[str, Any]]:
    df = pd.DataFrame(rows)
    out: list[dict[str, Any]] = []
    for aid in range(8):
        g = df[(df["dataset"] == dataset) & (df["anchor_id"] == aid)] if not df.empty else pd.DataFrame()
        rho = g["rho_mm"].to_numpy(dtype=float) if not g.empty else np.empty(0)
        out.append(
            {
                "dataset": dataset,
                "anchor_id": aid,
                "anchor_label": ANCHORS[aid],
                "n": int(rho.size),
                "rho_mean_mm": float(np.nanmean(rho)) if rho.size else float("nan"),
                "rho_median_mm": percentile(rho, 50),
                "rho_rms_mm": rmse(rho),
                "rho_p95_mm": percentile(rho, 95),
                "positive_spike_rate_gt100": float(np.mean(rho > 100.0)) if rho.size else float("nan"),
                "positive_spike_rate_gt150": float(np.mean(rho > 150.0)) if rho.size else float("nan"),
                "rho_definition": "range_measured - ||p_solved-a_i|| - d_anchor_i - d_tag",
            }
        )
    return out


def combine_nlos(static_rows: list[dict[str, Any]], dynamic_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_static = {int(r["anchor_id"]): r for r in static_rows}
    by_dynamic = {int(r["anchor_id"]): r for r in dynamic_rows}
    out: list[dict[str, Any]] = []
    for aid in range(8):
        s = by_static.get(aid, {})
        d = by_dynamic.get(aid, {})
        out.append(
            {
                "anchor_id": aid,
                "anchor_label": ANCHORS[aid],
                "static_n": s.get("n", 0),
                "dynamic_n": d.get("n", 0),
                "static_rho_rms_mm": s.get("rho_rms_mm", float("nan")),
                "dynamic_rho_rms_mm": d.get("rho_rms_mm", float("nan")),
                "delta_dynamic_minus_static_rms_mm": float(d.get("rho_rms_mm", float("nan")) - s.get("rho_rms_mm", float("nan"))),
                "static_positive_spike_rate_gt100": s.get("positive_spike_rate_gt100", float("nan")),
                "dynamic_positive_spike_rate_gt100": d.get("positive_spike_rate_gt100", float("nan")),
                "static_positive_spike_rate_gt150": s.get("positive_spike_rate_gt150", float("nan")),
                "dynamic_positive_spike_rate_gt150": d.get("positive_spike_rate_gt150", float("nan")),
            }
        )
    return out


def build_delay_comparison_rows(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    raw_v5 = inputs.get("raw_v5", {})
    raw_v4 = inputs.get("raw_v4", {})
    extra_v5 = raw_v5.get("extra", {}) if isinstance(raw_v5, dict) else {}
    common_v5 = extra_v5.get("common_mode_mm")
    diff_v5 = extra_v5.get("differential_delay_mm") or []
    rows: list[dict[str, Any]] = []
    for aid, label in enumerate(ANCHORS):
        d_v4 = float(inputs["delays_v4"].get(aid, 0.0))
        d_v5 = float(inputs["delays_v5"].get(aid, 0.0))
        e_i: float | str = ""
        if aid < len(diff_v5):
            e_i = float(diff_v5[aid])
        rows.append(
            {
                "anchor_id": aid,
                "anchor_label": label,
                "v4_d_anchor_mm": d_v4,
                "v5_d_anchor_mm": d_v5,
                "v5_minus_v4_d_anchor_mm": d_v5 - d_v4,
                "v5_common_mode_mm": "" if common_v5 is None else float(common_v5),
                "v5_differential_e_i_mm": e_i,
                "v5_delay_parameterization": extra_v5.get("delay_parameterization", ""),
                "v5_e_reg_scale_mm": extra_v5.get("e_reg_scale_mm", ""),
                "v4_tag_delay_mm": float(raw_v4.get("tag_delay_mm", 0.0)) if isinstance(raw_v4, dict) else 0.0,
                "v5_tag_delay_mm": float(raw_v5.get("tag_delay_mm", 0.0)) if isinstance(raw_v5, dict) else 0.0,
            }
        )
    return rows


def estimate_delay_independent(anchor_coords: np.ndarray) -> tuple[dict[int, float], list[dict[str, Any]]]:
    df = pd.read_csv(PAIR_QUALITY)
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
        vec = np.zeros(8)
        vec[ia] = 1.0
        vec[ib] = 1.0
        design.append(vec)
        target.append(bias)
        rows.append({"pair": f"{a}-{b}", "measured_median_mm": measured, "geometric_mm": geom, "bias_mm": bias})
    x, *_ = np.linalg.lstsq(np.vstack(design), np.asarray(target), rcond=None)
    delays = {i: float(x[i]) for i in range(8)}
    for row in rows:
        a, b = row["pair"].split("-")
        pred = delays[ANCHORS.index(a)] + delays[ANCHORS.index(b)]
        row["predicted_bias_mm"] = float(pred)
        row["residual_mm"] = float(pred - row["bias_mm"])
    return delays, rows


def estimate_delay_common_mode(anchor_coords: np.ndarray, e_reg_mm: float = 20.0) -> tuple[dict[int, float], dict[str, Any], list[dict[str, Any]]]:
    df = pd.read_csv(PAIR_QUALITY)
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
    weight = 1.0 / max(float(e_reg_mm), 1e-9)
    for i in range(8):
        vec = np.zeros(9)
        vec[1 + i] = weight
        design.append(vec)
        target.append(0.0)
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
        res = float(pred - row["bias_mm"])
        row["predicted_bias_mm"] = float(pred)
        row["residual_mm"] = res
        residuals.append(res)
    meta = {
        "common_mode_mm": c,
        "e_reg_mm": float(e_reg_mm),
        "mean_e_mm": float(np.mean(e)),
        "max_abs_e_mm": float(np.max(np.abs(e))),
        "pair_residual_rms_mm": rmse(residuals),
    }
    return delays, meta, rows


def compute_dop_rows(static_rows: list[dict[str, Any]], anchor_coords: np.ndarray) -> list[dict[str, Any]]:
    out = []
    for row in static_rows:
        p = np.asarray([row["truth_x_mm"], row["truth_y_vertical_mm"], row["truth_z_mm"]], dtype=float)
        dif = p[None, :] - anchor_coords
        dist = np.linalg.norm(dif, axis=1)
        good = np.isfinite(dist) & (dist > 1e-6)
        h = dif[good] / dist[good, None]
        if h.shape[0] < 4:
            gdop = hdop = vdop = cond = float("nan")
        else:
            q = np.linalg.pinv(h.T @ h)
            gdop = math.sqrt(max(0.0, float(np.trace(q))))
            hdop = math.sqrt(max(0.0, float(q[0, 0] + q[2, 2])))
            vdop = math.sqrt(max(0.0, float(q[1, 1])))
            cond = float(np.linalg.cond(h.T @ h))
        out.append(
            {
                "ID": row["ID"],
                "location": row.get("location", ""),
                "height": row.get("height", ""),
                "facing": row.get("facing", ""),
                "x_mm": float(p[0]),
                "y_vertical_mm": float(p[1]),
                "z_mm": float(p[2]),
                "gdop": gdop,
                "hdop": hdop,
                "vdop": vdop,
                "condition": cond,
            }
        )
    return out


def compute_drift_rows(static_files: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in static_files:
        sid = static_id_from_path(path)
        frames = read_tr_all_frames(path, tags={STATIC_TAG}, min_anchors=4)
        by_anchor: dict[int, list[tuple[float, float]]] = {aid: [] for aid in range(8)}
        for frame in frames:
            for obs in frame.observations:
                if 0 <= obs.anchor_id < 8 and obs.range_mm > 0:
                    by_anchor[int(obs.anchor_id)].append((float(frame.host_elapsed_s), float(obs.range_mm)))
        for aid, vals in by_anchor.items():
            if len(vals) < 10:
                continue
            arr = np.asarray(vals, dtype=float)
            t = arr[:, 0]
            r = arr[:, 1]
            if np.nanmax(t) <= np.nanmin(t):
                continue
            slope_s, intercept = np.polyfit(t, r, 1)
            duration_min = (float(np.nanmax(t)) - float(np.nanmin(t))) / 60.0
            rows.append(
                {
                    "ID": sid,
                    "anchor_id": aid,
                    "anchor_label": ANCHORS[aid],
                    "n_samples": int(len(vals)),
                    "duration_min": duration_min,
                    "range_slope_mm_per_min": float(slope_s * 60.0),
                    "abs_drift_over_capture_mm": abs(float(slope_s * 60.0)) * duration_min,
                }
            )
    return rows


def phase_context(name: str) -> dict[str, Any]:
    return {
        "phase": name,
        "physical_cores": psutil.cpu_count(logical=False) or 6,
        "logical_cores": psutil.cpu_count(logical=True) or os.cpu_count() or 6,
        "workers": 6,
        "started": time.perf_counter(),
        "cpu_samples": [],
    }


def finish_phase(ctx: dict[str, Any]) -> dict[str, Any]:
    elapsed = time.perf_counter() - float(ctx["started"])
    samples = np.asarray(ctx.get("cpu_samples") or [psutil.cpu_percent(interval=0.1)], dtype=float)
    return {
        "phase": ctx["phase"],
        "physical_cores": ctx["physical_cores"],
        "logical_cores": ctx["logical_cores"],
        "workers": ctx["workers"],
        "elapsed_s": elapsed,
        "mean_cpu_percent": float(np.nanmean(samples)),
        "max_cpu_percent": float(np.nanmax(samples)),
    }


def run_pool(jobs: list[dict[str, Any]], worker_fn, ctx: dict[str, Any], label: str) -> list[dict[str, Any]]:
    if not jobs:
        return []
    results: list[dict[str, Any]] = []
    mp_ctx = get_context("spawn")
    with ProcessPoolExecutor(max_workers=6, mp_context=mp_ctx) as pool:
        futures = [pool.submit(worker_fn, job) for job in jobs]
        done = 0
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            cpu_now = float(psutil.cpu_percent(interval=0.25))
            ctx.setdefault("cpu_samples", []).append(cpu_now)
            print(json.dumps({"stage": label, "done": done, "total": len(futures), "live_cpu_percent": cpu_now}, sort_keys=True), flush=True)
    return results


def make_dirs() -> dict[str, Path]:
    dirs = {
        "phase1": ANALYSIS / "FULL_V5_scale_to_vicon",
        "phase2": ANALYSIS / "FULL_V5",
        "phase3": ANALYSIS / "FULL_V5_align_to_Vicon",
        "phase4": ANALYSIS / "FULL_V5_one_baseline_scale_correction",
        "phase5": ANALYSIS / "FULL_transfer_matrix",
        "phase6": ANALYSIS / "FULL_V4_vs_V5_final",
    }
    for key, root in dirs.items():
        subs = ["tables", "reports"] if key == "phase6" else ["scripts", "tables", "reports"]
        for sub in subs:
            (root / sub).mkdir(parents=True, exist_ok=True)
    for key in ("phase1", "phase2", "phase3", "phase4", "phase5"):
        dst = dirs[key] / "scripts/run_full_v5_ablation_pipeline.py"
        if dst.resolve() != THIS:
            shutil.copy2(THIS, dst)
    return dirs


def prepare_inputs() -> dict[str, Any]:
    required = [
        (V5_LAYOUT, "V5-commonmode layout"),
        (SIGMA_PATH, "anchor sigma"),
        (PAIR_QUALITY, "pair quality solve table"),
        (STATIC_TABLE, "static_all_captures table"),
        (CAPTURES_ROOT, "capture root"),
        (OPTI_ROOT, "OptiTrack full root"),
        (FULL_ROOT, "existing FULL analysis"),
        (ANALYSIS / "FULL_AutoPos_align_to_Vicon", "existing FULL_AutoPos_align_to_Vicon"),
        (ANALYSIS / "FULL_AutoPos_scale_to_vicon", "existing FULL_AutoPos_scale_to_vicon"),
        (ANALYSIS / "FULL_AutoPos_one_baseline_scale_correction", "existing FULL_AutoPos_one_baseline_scale_correction"),
        (FULL_4WAY, "existing FULL_4way_comparison"),
        (ABLATION_SCRIPT, "static ablation helper script"),
        (OFFSETS_PATH, "existing ROTO time offsets"),
        (MAPPING_PATH, "existing ROTO wand mapping"),
    ]
    for path, label in required:
        require_path(path, label)
    v4_layout = find_v4_layout()
    ab = load_module(ABLATION_SCRIPT, "full_v5_pipeline_ablation_main")
    labels_v5, coords_v5, delays_v5, tag_delay_v5, raw_v5 = load_layout_raw(V5_LAYOUT)
    labels_v4, coords_v4, delays_v4, tag_delay_v4, raw_v4 = load_layout_raw(v4_layout)
    if set(labels_v5) != set(ANCHORS) or set(labels_v4) != set(ANCHORS):
        raise RuntimeError(f"unexpected anchor labels: V5={labels_v5}, V4={labels_v4}")
    by_v5 = {label: coords_v5[i] for i, label in enumerate(labels_v5)}
    by_v4 = {label: coords_v4[i] for i, label in enumerate(labels_v4)}
    coords_v5 = np.vstack([by_v5[a] for a in ANCHORS])
    coords_v4 = np.vstack([by_v4[a] for a in ANCHORS])
    anchor_truth, tag_truth_np, tag_truth_meta, _corr = ab.load_corrected_static_truth(OPTI_ROOT, ANCHORS, PRIMARY_IDS)
    truth_coords = np.vstack([anchor_truth[a] for a in ANCHORS])
    static_files = sorted(CAPTURES_ROOT.glob("static_ID*/tag_capture*/tr_all.csv"))
    if len(static_files) != 24:
        raise RuntimeError(f"expected 24 static captures under {CAPTURES_ROOT}, found {len(static_files)}: {[str(p) for p in static_files]}")
    roto_files = discover_roto_files()
    if len(roto_files) != 17:
        raise RuntimeError(f"expected 17 ROTO captures under {CAPTURES_ROOT}, found {len(roto_files)}: {roto_files}")
    for cid in roto_files:
        require_path(OPTI_ROOT / f"{cid}.trc", f"OptiTrack TRC for {cid}")
    metadata_by_id = ab.load_static_metadata(STATIC_TABLE)
    sigma_by_id = ab.load_anchor_sigma(SIGMA_PATH)
    return {
        "ablation": ab,
        "v4_layout_path": v4_layout,
        "v5_layout_path": V5_LAYOUT,
        "coords_v4": coords_v4,
        "coords_v5": coords_v5,
        "delays_v4": delays_v4,
        "delays_v5": delays_v5,
        "raw_v4": raw_v4,
        "raw_v5": raw_v5,
        "truth_coords": truth_coords,
        "anchor_truth": anchor_truth,
        "tag_truth": {k: v.tolist() for k, v in tag_truth_np.items()},
        "tag_truth_meta": tag_truth_meta,
        "metadata_by_id": metadata_by_id,
        "sigma_by_id": sigma_by_id,
        "static_files": static_files,
        "roto_files": roto_files,
        "mapping": read_mapping(MAPPING_PATH),
        "offsets": read_offsets(OFFSETS_PATH),
        "opti_by_capture": {cid: parse_trc_trajectories(OPTI_ROOT / f"{cid}.trc", ROTO_MARKERS) for cid in roto_files},
    }


def write_transform_table(path: Path, name: str, fit: Fit, labels: list[str], truth: np.ndarray) -> list[dict[str, Any]]:
    q = rotation_to_quaternion(fit.rotation)
    residual = np.linalg.norm(fit.aligned - truth, axis=1)
    rows: list[dict[str, Any]] = [
        {
            "row_type": "transform",
            "name": name,
            "scale": fit.scale,
            "det": fit.det,
            "qw": q[0],
            "qx": q[1],
            "qy": q[2],
            "qz": q[3],
            "tx_mm": fit.translation[0],
            "ty_mm": fit.translation[1],
            "tz_mm": fit.translation[2],
            "anchor_rmse_mm": rmse(residual),
            "anchor_median_mm": percentile(residual, 50),
            "anchor_p95_mm": percentile(residual, 95),
        }
    ]
    for i, label in enumerate(labels):
        rows.append(
            {
                "row_type": "anchor",
                "name": name,
                "anchor_id": i,
                "anchor_label": label,
                "aligned_x_mm": fit.aligned[i, 0],
                "aligned_y_mm": fit.aligned[i, 1],
                "aligned_z_mm": fit.aligned[i, 2],
                "truth_x_mm": truth[i, 0],
                "truth_y_mm": truth[i, 1],
                "truth_z_mm": truth[i, 2],
                "residual_3d_mm": residual[i],
            }
        )
    write_csv(path, rows)
    return rows


def run_phase1(dirs: dict[str, Path], inputs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Fit]]:
    ctx = phase_context("Phase 1")
    out = dirs["phase1"]
    truth = inputs["truth_coords"]
    v5_sim3 = fit_similarity(inputs["coords_v5"], truth, allow_reflection=True, allow_scale=True)
    v5_rigid = fit_similarity(inputs["coords_v5"], truth, allow_reflection=True, allow_scale=False)
    v4_sim3 = fit_similarity(inputs["coords_v4"], truth, allow_reflection=True, allow_scale=True)
    v4_rigid = fit_similarity(inputs["coords_v4"], truth, allow_reflection=True, allow_scale=False)
    write_transform_table(out / "tables/v5_sim3_transform.csv", "v5_sim3", v5_sim3, ANCHORS, truth)
    write_transform_table(out / "tables/v5_rigid_transform.csv", "v5_rigid", v5_rigid, ANCHORS, truth)
    rows = []
    for name, sim3, rigid in [("v4-io", v4_sim3, v4_rigid), ("v5-commonmode", v5_sim3, v5_rigid)]:
        rows.append(
            {
                "layout": name,
                "sim3_scale": sim3.scale,
                "sim3_anchor_rmse_mm": rmse(np.linalg.norm(sim3.aligned - truth, axis=1)),
                "rigid_anchor_rmse_mm": rmse(np.linalg.norm(rigid.aligned - truth, axis=1)),
                "rigid_anchor_median_mm": percentile(np.linalg.norm(rigid.aligned - truth, axis=1), 50),
                "rigid_anchor_p95_mm": percentile(np.linalg.norm(rigid.aligned - truth, axis=1), 95),
            }
        )
    write_csv(out / "tables/v5_vs_v4_scale_comparison.csv", rows)
    lines = ["# PHASE 1 - V5 Scale To Vicon\n\n"]
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")
    append_md_table(lines, rows, ["layout", "sim3_scale", "sim3_anchor_rmse_mm", "rigid_anchor_rmse_mm", "rigid_anchor_median_mm", "rigid_anchor_p95_mm"])
    report = finish_phase(ctx)
    lines.append("## Runtime\n\n")
    append_md_table(lines, [report], ["physical_cores", "logical_cores", "workers", "elapsed_s", "mean_cpu_percent", "max_cpu_percent"])
    (out / "reports/PHASE1_V5_SCALE.md").write_text("".join(lines), encoding="utf-8")
    print("".join(lines), flush=True)
    return report, {"v5_rigid": v5_rigid, "v5_sim3": v5_sim3, "v4_rigid": v4_rigid, "v4_sim3": v4_sim3}


def run_phase2(dirs: dict[str, Path], inputs: dict[str, Any], fits: dict[str, Fit], run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Phase 2")
    out = dirs["phase2"]
    coords = fits["v5_rigid"].aligned
    anchor_centroid = inputs["truth_coords"].mean(axis=0)
    base_job = {
        "ablation_script": str(ABLATION_SCRIPT),
        "coords": coords.tolist(),
        "delays": inputs["delays_v5"],
        "sigma_by_id": inputs["sigma_by_id"],
        "tag_truth": inputs["tag_truth"],
        "tag_truth_meta": inputs["tag_truth_meta"],
        "metadata_by_id": inputs["metadata_by_id"],
        "static_files": [str(p) for p in inputs["static_files"]],
        "anchor_centroid": anchor_centroid.tolist(),
        "layout_name": "v5-commonmode-rigid-selfcal",
        "layout_source": "L_V5",
        "method": "T4",
        "point_estimator": "mean",
    }
    dloo = 49.621
    grid = [float(x) for x in np.arange(0.0, 120.0 + 1.0, 2.0)]
    jobs = []
    for dtag in sorted(set([0.0, dloo] + grid)):
        mode = "D0" if abs(dtag) < 1e-9 else ("D_LOO_CV" if abs(dtag - dloo) < 1e-6 else "D_sweep_grid")
        jobs.append({**base_job, "cell_id": f"phase2_static_{dtag:.3f}", "d_tag_mm": dtag, "tag_delay_mode": mode, "need_rho": abs(dtag - dloo) < 1e-6})
    results = run_pool(jobs, static_cell_worker, ctx, "phase2_static")
    by_dtag = {round(float(r["d_tag_mm"]), 6): r for r in results}
    sweep_rows = []
    for dtag in grid:
        r = by_dtag[round(dtag, 6)]
        sweep_rows.append({"d_tag_mm": dtag, **r["summary"]})
    write_csv(out / "tables/dtag_sweep_v5.csv", sweep_rows)
    best = min(sweep_rows, key=lambda r: (float(r["median_3d_mm"]), float(r["p95_3d_mm"])))
    variants = [
        ("D0", 0.0, by_dtag[0.0], "static_summary_D0.csv"),
        ("D_LOO_CV", dloo, by_dtag[round(dloo, 6)], "static_summary_DLOO.csv"),
        ("D_sweep_opt", float(best["d_tag_mm"]), by_dtag[round(float(best["d_tag_mm"]), 6)], "static_summary_Dsweep.csv"),
    ]
    per_position: list[dict[str, Any]] = []
    unified: list[dict[str, Any]] = []
    for mode, dtag, result, filename in variants:
        summary = {
            "layout_source": "L_V5",
            "correction_source": "C_V5",
            "tag_delay_mode": mode,
            "tag_delay_value_mm": dtag,
            **result["summary"],
        }
        write_csv(out / f"tables/{filename}", [summary])
        for row in result["rows"]:
            per_position.append({"tag_delay_mode": mode, "tag_delay_value_mm": dtag, **row})
        unified.append(
            unified_row(
                run_id=run_id,
                phase="Phase2_static",
                layout_source="L_V5",
                correction_source="C_V5",
                correction_detail="V5 common-mode self-cal delays",
                tag_delay_mode=mode,
                tag_delay_value_mm=dtag,
                tag_solver="T4",
                summary=result["summary"],
                notes="static mean-position estimator; V5 rigid anchor lock, no scale",
            )
        )
    write_csv(out / "tables/static_per_position.csv", per_position)
    for group_col, filename in [("height", "static_by_height.csv"), ("facing", "static_by_facing.csv")]:
        g_rows = []
        df = pd.DataFrame(per_position)
        if not df.empty and group_col in df:
            for key, g in df.groupby(["tag_delay_mode", group_col], dropna=False):
                mode, group = key
                agg = aggregate_static_rows(g.to_dict("records"), expected_positions=len(g))
                g_rows.append({"tag_delay_mode": mode, group_col: group, **agg})
        write_csv(out / f"tables/{filename}", g_rows)
    rho_static = []
    for result in results:
        if abs(float(result["d_tag_mm"]) - dloo) < 1e-6:
            rho_static.extend(result.get("rho_rows", []))
    residual_static = summarize_rho(rho_static, "static")
    write_csv(out / "tables/per_anchor_residual_static.csv", residual_static)
    delay_rows = build_delay_comparison_rows(inputs)
    write_csv(out / "tables/delay_comparison_v4_vs_v5.csv", delay_rows)
    dop_rows = compute_dop_rows(by_dtag[round(dloo, 6)]["rows"], coords)
    write_csv(out / "tables/dop_per_position.csv", dop_rows)
    drift_rows = compute_drift_rows(inputs["static_files"])
    drift_summary = [
        {
            "n_anchor_sessions": len(drift_rows),
            "median_abs_drift_over_capture_mm": percentile([r["abs_drift_over_capture_mm"] for r in drift_rows], 50),
            "p95_abs_drift_over_capture_mm": percentile([r["abs_drift_over_capture_mm"] for r in drift_rows], 95),
            "median_abs_slope_mm_per_min": percentile([abs(r["range_slope_mm_per_min"]) for r in drift_rows], 50),
            "p95_abs_slope_mm_per_min": percentile([abs(r["range_slope_mm_per_min"]) for r in drift_rows], 95),
        }
    ]
    write_csv(out / "tables/drift_summary.csv", drift_summary)

    layout_dir = out / "tables/generated_layouts"
    layout_d0 = layout_dir / "v5_commonmode_d0.json"
    layout_dloo = layout_dir / "v5_commonmode_dloo.json"
    write_layout_json(layout_d0, name="v5_commonmode_d0", coords=inputs["coords_v5"], delays=inputs["delays_v5"], tag_delay_mm=0.0)
    write_layout_json(layout_dloo, name="v5_commonmode_dloo", coords=inputs["coords_v5"], delays=inputs["delays_v5"], tag_delay_mm=0.0)
    roto_jobs = []
    for mode, dtag, layout_path in [("D0", 0.0, layout_d0), ("D_LOO_CV", dloo, layout_dloo)]:
        for cid, cap_path in inputs["roto_files"].items():
            for tag in ROTO_TAGS:
                roto_jobs.append(
                    {
                        "track_id": f"{mode}_{cid}_{tag}",
                        "layout_path": str(layout_path),
                        "capture_path": str(cap_path),
                        "sigma_path": str(SIGMA_PATH),
                        "layout_source": "L_V5",
                        "correction_source": "C_V5",
                        "tag_delay_mode": mode,
                        "d_tag_mm": dtag,
                        "capture_id": cid,
                        "tag": tag,
                        "method": "T4",
                        "need_rho": mode == "D_LOO_CV",
                    }
                )
    solved_roto = run_pool(roto_jobs, solve_roto_track_worker, ctx, "phase2_roto")
    fit_raw_to_vicon = Fit(fits["v5_rigid"].rotation, fits["v5_rigid"].translation, fits["v5_rigid"].scale, fits["v5_rigid"].det, fits["v5_rigid"].aligned)
    track_rows, circle_rows, roto_summary = evaluate_roto_tracks(
        solved_roto,
        fit=fit_raw_to_vicon,
        opti_by_capture=inputs["opti_by_capture"],
        mapping=inputs["mapping"],
        offsets=inputs["offsets"],
        alignment_note="BEST-FIT-ALIGNED: V5 self-cal anchors rigidly aligned to Vicon; capture-level v4-io/T4 time offsets reused",
    )
    write_csv(out / "tables/roto_track_summary.csv", track_rows + roto_summary)
    write_csv(out / "tables/roto_circle_fit.csv", circle_rows)
    rho_dynamic = []
    for r in solved_roto:
        if r["tag_delay_mode"] == "D_LOO_CV":
            rho_dynamic.extend(r.get("rho_rows", []))
    residual_dynamic = summarize_rho(rho_dynamic, "dynamic")
    write_csv(out / "tables/per_anchor_residual_dynamic.csv", residual_dynamic)
    write_csv(out / "tables/per_anchor_nlos_comparison.csv", combine_nlos(residual_static, residual_dynamic))
    for row in roto_summary:
        unified.append(
            unified_row(
                run_id=run_id,
                phase="Phase2_roto",
                layout_source=row["layout_source"],
                correction_source=row["correction_source"],
                correction_detail="V5 common-mode self-cal delays",
                tag_delay_mode=row["tag_delay_mode"],
                tag_delay_value_mm=float(row["d_tag_mm"]),
                tag_solver="T4",
                summary=row,
                notes="ROTO BEST-FIT-ALIGNED; no hardware time sync",
            )
        )
    write_csv(out / "tables/unified_results.csv", unified, UNIFIED_COLUMNS)
    report = finish_phase(ctx)
    lines = ["# PHASE 2 - FULL V5\n\n"]
    lines.append("## Static Summary\n\n")
    append_md_table(lines, [u for u in unified if u["phase"] == "Phase2_static"], ["tag_delay_mode", "tag_delay_value_mm", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_vert_mm", "signed_vertical_slope_mm_per_m"])
    lines.append("## Delay Comparison\n\n")
    append_md_table(lines, delay_rows, ["anchor_label", "v4_d_anchor_mm", "v5_d_anchor_mm", "v5_minus_v4_d_anchor_mm", "v5_common_mode_mm", "v5_differential_e_i_mm"])
    lines.append("## ROTO Summary\n\n")
    append_md_table(lines, [u for u in unified if u["phase"] == "Phase2_roto"], ["tag_delay_mode", "tag_delay_value_mm", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_vert_mm", "notes"])
    lines.append("## Runtime\n\n")
    append_md_table(lines, [report], ["physical_cores", "logical_cores", "workers", "elapsed_s", "mean_cpu_percent", "max_cpu_percent"])
    (out / "reports/PHASE2_FULL_V5.md").write_text("".join(lines), encoding="utf-8")
    print("".join(lines), flush=True)
    return report, unified


def run_phase3(dirs: dict[str, Path], inputs: dict[str, Any], run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, float]]:
    ctx = phase_context("Phase 3")
    out = dirs["phase3"]
    truth = inputs["truth_coords"]
    indep_delays, indep_rows = estimate_delay_independent(truth)
    cm_delays, cm_meta, cm_rows = estimate_delay_common_mode(truth, e_reg_mm=20.0)
    write_csv(out / "tables/vicon_anchor_delays_refit_indep.csv", [{"anchor_id": i, "anchor_label": ANCHORS[i], "d_anchor_mm": indep_delays[i]} for i in range(8)] + indep_rows)
    write_csv(out / "tables/vicon_anchor_delays_refit_cm.csv", [{"row_type": "summary", **cm_meta}] + [{"row_type": "anchor", "anchor_id": i, "anchor_label": ANCHORS[i], "d_anchor_mm": cm_delays[i], "e_i_mm": cm_delays[i] - cm_meta["common_mode_mm"]} for i in range(8)] + cm_rows)
    corrections = {
        "C_none": {i: 0.0 for i in range(8)},
        "C_V4_transfer": inputs["delays_v4"],
        "C_V5_transfer": inputs["delays_v5"],
        "C_Vicon_refit_indep": indep_delays,
        "C_Vicon_refit_cm": cm_delays,
    }
    base_job = {
        "ablation_script": str(ABLATION_SCRIPT),
        "coords": truth.tolist(),
        "sigma_by_id": inputs["sigma_by_id"],
        "tag_truth": inputs["tag_truth"],
        "tag_truth_meta": inputs["tag_truth_meta"],
        "metadata_by_id": inputs["metadata_by_id"],
        "static_files": [str(p) for p in inputs["static_files"]],
        "anchor_centroid": truth.mean(axis=0).tolist(),
        "layout_name": "vicon_truth_anchor_layout",
        "layout_source": "L_Vicon",
        "method": "T4",
        "point_estimator": "mean",
        "need_rho": False,
    }
    jobs = []
    for corr, delays in corrections.items():
        for mode, dtag in [("D0", 0.0), ("D_LOO_CV", 49.621)]:
            jobs.append({**base_job, "cell_id": f"phase3_{corr}_{mode}", "delays": delays, "d_tag_mm": dtag, "tag_delay_mode": mode, "metadata": {"correction_source": corr}})
    results = run_pool(jobs, static_cell_worker, ctx, "phase3_static")
    unified: list[dict[str, Any]] = []
    static_rows = []
    for result in results:
        corr = result["metadata"].get("correction_source", "")
        mode = result["tag_delay_mode"]
        dtag = float(result["d_tag_mm"])
        row = {
            "layout_source": "L_Vicon",
            "correction_source": corr,
            "correction_detail": "Vicon anchor coordinates; correction as named",
            "tag_delay_mode": mode,
            "tag_delay_value_mm": dtag,
            **result["summary"],
        }
        static_rows.append(row)
        unified.append(
            unified_row(
                run_id=run_id,
                phase="Phase3_static",
                layout_source="L_Vicon",
                correction_source=corr,
                correction_detail="Vicon anchor coords with delay variant",
                tag_delay_mode=mode,
                tag_delay_value_mm=dtag,
                tag_solver="T4",
                summary=result["summary"],
                notes="known-anchor Vicon control",
            )
        )
    write_csv(out / "tables/vicon_anchor_static_results.csv", static_rows)
    layout_dir = out / "tables/generated_layouts"
    roto_layouts = {}
    for mode, dtag in [("D0", 0.0), ("D_LOO_CV", 49.621)]:
        p = layout_dir / f"vicon_cm_{mode}.json"
        write_layout_json(p, name=f"vicon_cm_{mode}", coords=truth, delays=cm_delays, tag_delay_mm=0.0)
        roto_layouts[mode] = p
    roto_jobs = []
    for mode, dtag in [("D0", 0.0), ("D_LOO_CV", 49.621)]:
        for cid, cap_path in inputs["roto_files"].items():
            for tag in ROTO_TAGS:
                roto_jobs.append(
                    {
                        "track_id": f"phase3_{mode}_{cid}_{tag}",
                        "layout_path": str(roto_layouts[mode]),
                        "capture_path": str(cap_path),
                        "sigma_path": str(SIGMA_PATH),
                        "layout_source": "L_Vicon",
                        "correction_source": "C_Vicon_refit_cm",
                        "tag_delay_mode": mode,
                        "d_tag_mm": dtag,
                        "capture_id": cid,
                        "tag": tag,
                        "method": "T4",
                        "need_rho": False,
                    }
                )
    solved = run_pool(roto_jobs, solve_roto_track_worker, ctx, "phase3_roto")
    track_rows, _circle_rows, roto_summary = evaluate_roto_tracks(
        solved,
        fit=None,
        opti_by_capture=inputs["opti_by_capture"],
        mapping=inputs["mapping"],
        offsets=inputs["offsets"],
        alignment_note="BEST-FIT-ALIGNED: Vicon anchor coords; capture-level v4-io/T4 time offsets reused",
    )
    write_csv(out / "tables/vicon_anchor_roto_results.csv", track_rows + roto_summary)
    for row in roto_summary:
        unified.append(
            unified_row(
                run_id=run_id,
                phase="Phase3_roto",
                layout_source="L_Vicon",
                correction_source="C_Vicon_refit_cm",
                correction_detail="Vicon-frame common-mode delay refit e_reg=20",
                tag_delay_mode=row["tag_delay_mode"],
                tag_delay_value_mm=float(row["d_tag_mm"]),
                tag_solver="T4",
                summary=row,
                notes="ROTO known-anchor control; BEST-FIT-ALIGNED",
            )
        )
    write_csv(out / "tables/unified_results.csv", unified, UNIFIED_COLUMNS)
    report = finish_phase(ctx)
    lines = ["# PHASE 3 - V5 Align To Vicon\n\n", "## Static Known-Anchor Results\n\n"]
    append_md_table(lines, sorted(static_rows, key=lambda r: (r["correction_source"], r["tag_delay_mode"])), ["correction_source", "tag_delay_mode", "tag_delay_value_mm", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_vert_mm", "signed_vertical_slope_mm_per_m"])
    lines.append("## Runtime\n\n")
    append_md_table(lines, [report], ["physical_cores", "logical_cores", "workers", "elapsed_s", "mean_cpu_percent", "max_cpu_percent"])
    (out / "reports/PHASE3_V5_ALIGN_TO_VICON.md").write_text("".join(lines), encoding="utf-8")
    print("".join(lines), flush=True)
    return report, unified, cm_delays


def run_phase4(dirs: dict[str, Path], inputs: dict[str, Any], run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Phase 4")
    out = dirs["phase4"]
    truth = inputs["truth_coords"]
    median_uncorrected = float("nan")
    phase2_dloo = ANALYSIS / "FULL_V5/tables/static_summary_DLOO.csv"
    if phase2_dloo.exists():
        median_uncorrected = float(pd.read_csv(phase2_dloo).iloc[0]["median_3d_mm"])
    jobs = []
    for a_i in range(8):
        for b_i in range(a_i + 1, 8):
            pair = f"{ANCHORS[a_i]}-{ANCHORS[b_i]}"
            d_v5 = float(np.linalg.norm(inputs["coords_v5"][a_i] - inputs["coords_v5"][b_i]))
            d_true = float(np.linalg.norm(truth[a_i] - truth[b_i]))
            scale = d_true / d_v5
            fit = fit_with_fixed_scale(inputs["coords_v5"], truth, scale)
            delays = {i: float(inputs["delays_v5"][i] * scale) for i in range(8)}
            jobs.append(
                {
                    "ablation_script": str(ABLATION_SCRIPT),
                    "coords": fit.aligned.tolist(),
                    "delays": delays,
                    "sigma_by_id": inputs["sigma_by_id"],
                    "tag_truth": inputs["tag_truth"],
                    "tag_truth_meta": inputs["tag_truth_meta"],
                    "metadata_by_id": inputs["metadata_by_id"],
                    "static_files": [str(p) for p in inputs["static_files"]],
                    "anchor_centroid": truth.mean(axis=0).tolist(),
                    "layout_name": f"v5_one_baseline_{pair}",
                    "layout_source": "L_V5",
                    "method": "T4",
                    "point_estimator": "mean",
                    "need_rho": False,
                    "cell_id": f"phase4_v5_{pair}",
                    "d_tag_mm": 49.621,
                    "tag_delay_mode": "D_LOO_CV",
                    "metadata": {"baseline_pair": pair, "scale_factor": scale, "d_v5_mm": d_v5, "d_vicon_mm": d_true},
                }
            )
    results = run_pool(jobs, static_cell_worker, ctx, "phase4_v5_pairs")
    v5_rows = []
    unified: list[dict[str, Any]] = []
    for result in results:
        meta = result["metadata"]
        row = {
            "pair": meta["baseline_pair"],
            "scale_factor": meta["scale_factor"],
            "baseline_v5_dist_mm": meta["d_v5_mm"],
            "baseline_vicon_dist_mm": meta["d_vicon_mm"],
            "median_3d": result["summary"]["median_3d_mm"],
            "rmse_3d": result["summary"]["rmse_3d_mm"],
            "p95_3d": result["summary"]["p95_3d_mm"],
            "delta_vs_uncorrected": float(result["summary"]["median_3d_mm"] - median_uncorrected) if math.isfinite(median_uncorrected) else float("nan"),
        }
        v5_rows.append(row)
        unified.append(
            unified_row(
                run_id=run_id,
                phase="Phase4_static",
                layout_source="L_V5",
                correction_source="C_V5_scaled_one_baseline",
                correction_detail=f"baseline={meta['baseline_pair']}; scale={meta['scale_factor']:.6f}",
                tag_delay_mode="D_LOO_CV",
                tag_delay_value_mm=49.621,
                tag_solver="T4",
                summary=result["summary"],
                notes="single-baseline V5 scale correction; V5 delays scaled proportionally",
            )
        )
    v5_rows = sorted(v5_rows, key=lambda r: (float(r["median_3d"]), float(r["p95_3d"])))
    write_csv(out / "tables/one_baseline_v5_all_pairs.csv", v5_rows)
    v4_existing = ANALYSIS / "FULL_AutoPos_one_baseline_scale_correction/tables/static_accuracy_summary.csv"
    v4_rows = []
    if v4_existing.exists():
        df = pd.read_csv(v4_existing)
        sub = df[(df["tag_method"] == "T4") & (df["delay_mode"] == "one_baseline_layout_inter_anchor_delaycal")]
        for _, r in sub.iterrows():
            v4_rows.append(
                {
                    "pair": r["baseline_pair"],
                    "scale_factor": r["scale_factor"],
                    "median_3d": r["err_3d_median_mm"],
                    "rmse_3d": r["err_3d_rms_mm"],
                    "p95_3d": r["err_3d_p95_mm"],
                    "source": str(v4_existing),
                }
            )
    write_csv(out / "tables/one_baseline_v4_all_pairs.csv", sorted(v4_rows, key=lambda r: (float(r["median_3d"]), float(r["p95_3d"]))))
    comparison = []
    if v5_rows:
        comparison.append({"layout": "V5", "best_pair": v5_rows[0]["pair"], "best_median_3d_mm": v5_rows[0]["median_3d"], "best_p95_3d_mm": v5_rows[0]["p95_3d"], "best_scale_factor": v5_rows[0]["scale_factor"]})
    if v4_rows:
        best_v4 = sorted(v4_rows, key=lambda r: (float(r["median_3d"]), float(r["p95_3d"])))[0]
        comparison.append({"layout": "V4", "best_pair": best_v4["pair"], "best_median_3d_mm": best_v4["median_3d"], "best_p95_3d_mm": best_v4["p95_3d"], "best_scale_factor": best_v4["scale_factor"]})
    write_csv(out / "tables/one_baseline_v5_vs_v4_comparison.csv", comparison)
    write_csv(out / "tables/unified_results.csv", unified, UNIFIED_COLUMNS)
    report = finish_phase(ctx)
    lines = ["# PHASE 4 - V5 One Baseline Scale Correction\n\n", "## Best Pair Comparison\n\n"]
    append_md_table(lines, comparison, ["layout", "best_pair", "best_median_3d_mm", "best_p95_3d_mm", "best_scale_factor"])
    lines.append("## Runtime\n\n")
    append_md_table(lines, [report], ["physical_cores", "logical_cores", "workers", "elapsed_s", "mean_cpu_percent", "max_cpu_percent"])
    (out / "reports/PHASE4_V5_ONE_BASELINE.md").write_text("".join(lines), encoding="utf-8")
    print("".join(lines), flush=True)
    return report, unified


def run_phase5(dirs: dict[str, Path], inputs: dict[str, Any], fits: dict[str, Fit], cm_delays: dict[int, float], run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = phase_context("Phase 5")
    out = dirs["phase5"]
    layouts = {
        "L_Vicon": inputs["truth_coords"],
        "L_V4": fits["v4_rigid"].aligned,
        "L_V5": fits["v5_rigid"].aligned,
    }
    corrections = {
        "C_none": {i: 0.0 for i in range(8)},
        "C_V4": inputs["delays_v4"],
        "C_V5": inputs["delays_v5"],
        "C_Vicon_cm": cm_delays,
    }
    fixed = {"D0": 0.0, "D_LOO_CV": 49.621, "D_oracle_91": 91.153}
    grid = [float(x) for x in np.arange(0.0, 120.0 + 1.0, 2.0)]
    jobs = []
    for lname, coords in layouts.items():
        for cname, delays in corrections.items():
            jobs.append(
                {
                    "combo_id": f"{lname}_{cname}",
                    "ablation_script": str(ABLATION_SCRIPT),
                    "coords": np.asarray(coords, dtype=float).tolist(),
                    "delays": delays,
                    "sigma_by_id": inputs["sigma_by_id"],
                    "tag_truth": inputs["tag_truth"],
                    "tag_truth_meta": inputs["tag_truth_meta"],
                    "metadata_by_id": inputs["metadata_by_id"],
                    "static_files": [str(p) for p in inputs["static_files"]],
                    "anchor_centroid": inputs["truth_coords"].mean(axis=0).tolist(),
                    "layout_name": f"{lname}_{cname}",
                    "layout_source": lname,
                    "correction_source": cname,
                    "correction_detail": f"{lname} with {cname}",
                    "method": "T4",
                    "point_estimator": "mean",
                    "need_rho": False,
                    "fixed_dtags": fixed,
                    "dtag_grid": grid,
                }
            )
    results = run_pool(jobs, transfer_combo_worker, ctx, "phase5_transfer_combo")
    summary_rows = []
    sweep_rows = []
    unified = []
    for result in results:
        summary_rows.extend(result["summary_rows"])
        sweep_rows.extend(result["sweep_rows"])
    for row in summary_rows:
        unified.append(
            unified_row(
                run_id=run_id,
                phase="Phase5_transfer",
                layout_source=row["layout_source"],
                correction_source=row["correction_source"],
                correction_detail=row["correction_detail"],
                tag_delay_mode=row["tag_delay_mode"],
                tag_delay_value_mm=float(row["tag_delay_value_mm"]),
                tag_solver="T4",
                summary=row,
                notes="transfer matrix static cell; D_sweep_opt is in-sample diagnostic",
            )
        )
    write_csv(out / "tables/transfer_matrix_48cells.csv", unified, UNIFIED_COLUMNS)
    write_csv(out / "tables/transfer_matrix_Dsweep_detail.csv", sweep_rows)
    write_csv(out / "tables/unified_results.csv", unified, UNIFIED_COLUMNS)
    def heatmap(mode: str, metric: str) -> list[dict[str, Any]]:
        rows = []
        df = pd.DataFrame(unified)
        sub = df[df["tag_delay_mode"] == mode]
        for lname in layouts:
            row = {"layout": lname}
            for cname in corrections:
                g = sub[(sub["layout_source"] == lname) & (sub["correction_source"] == cname)]
                row[cname] = float(g.iloc[0][metric]) if not g.empty else float("nan")
            rows.append(row)
        return rows
    heatmaps = {
        "median_D0": heatmap("D0", "median_3d_mm"),
        "median_DLOO": heatmap("D_LOO_CV", "median_3d_mm"),
        "median_Dsweep": heatmap("D_sweep_opt", "median_3d_mm"),
        "rmse_D0": heatmap("D0", "rmse_3d_mm"),
        "vertical_slope_D0": heatmap("D0", "signed_vertical_slope_mm_per_m"),
    }
    for name, rows in heatmaps.items():
        write_csv(out / f"tables/{name}.csv", rows)
    report = finish_phase(ctx)
    lines = ["# PHASE 5 - Transfer Matrix\n\n"]
    for title, rows in heatmaps.items():
        lines.append(f"## {title}\n\n")
        append_md_table(lines, rows, ["layout", *corrections.keys()])
    lines.append("## Runtime\n\n")
    append_md_table(lines, [report], ["physical_cores", "logical_cores", "workers", "elapsed_s", "mean_cpu_percent", "max_cpu_percent"])
    (out / "reports/PHASE5_TRANSFER_MATRIX.md").write_text("".join(lines), encoding="utf-8")
    print("".join(lines), flush=True)
    return report, unified


def load_csv_first(path: Path, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    df = pd.read_csv(path)
    if filters:
        for col, val in filters.items():
            df = df[df[col] == val]
    if df.empty:
        raise RuntimeError(f"no matching rows in {path} filters={filters}")
    return df.iloc[0].to_dict()


def run_phase6(dirs: dict[str, Path], all_unified: list[dict[str, Any]], runtime_rows: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    ctx = phase_context("Phase 6")
    out = dirs["phase6"]
    df = pd.DataFrame(all_unified)
    phase1_scale = pd.read_csv(ANALYSIS / "FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv")
    write_csv(out / "tables/final_scale_comparison.csv", phase1_scale.to_dict("records"))
    phase4_cmp = pd.read_csv(ANALYSIS / "FULL_V5_one_baseline_scale_correction/tables/one_baseline_v5_vs_v4_comparison.csv")
    write_csv(out / "tables/final_one_baseline_comparison.csv", phase4_cmp.to_dict("records"))
    existing_v4 = {"case": "V4 production static v4-io/T4", "median_3d_mm": 72.7, "p95_3d_mm": 171.5, "rmse_3d_mm": 109.8, "source": "FULL_4way_comparison report"}
    old_oracle = {"case": "old Vicon anchor control", "median_3d_mm": 64.1, "p95_3d_mm": 128.4, "source": "FULL_AutoPos_align_to_Vicon"}
    v5_rows = df[(df["phase"] == "Phase2_static") & (df["layout_source"] == "L_V5")]
    static_cmp = [existing_v4]
    for _, r in v5_rows.sort_values("tag_delay_mode").iterrows():
        static_cmp.append({"case": f"V5 {r['tag_delay_mode']}", "median_3d_mm": r["median_3d_mm"], "p95_3d_mm": r["p95_3d_mm"], "rmse_3d_mm": r["rmse_3d_mm"], "source": "Phase2"})
    write_csv(out / "tables/final_v4_vs_v5_static_comparison.csv", static_cmp)
    roto_cmp = [{"case": "V4 production ROTO v4-io/T4", "median_3d_mm": 105.8, "p95_3d_mm": 231.8, "source": "FULL/roto_absolute"}]
    for _, r in df[df["phase"] == "Phase2_roto"].iterrows():
        roto_cmp.append({"case": f"V5 ROTO {r['tag_delay_mode']}", "median_3d_mm": r["median_3d_mm"], "p95_3d_mm": r["p95_3d_mm"], "rmse_3d_mm": r["rmse_3d_mm"], "source": "Phase2"})
    write_csv(out / "tables/final_v4_vs_v5_roto_comparison.csv", roto_cmp)
    oracle_new = df[(df["phase"] == "Phase3_static") & (df["correction_source"] == "C_Vicon_refit_cm") & (df["tag_delay_mode"] == "D_LOO_CV")]
    oracle_rows = [old_oracle]
    if not oracle_new.empty:
        r = oracle_new.iloc[0]
        oracle_rows.append({"case": "updated V5-era Vicon cm oracle", "median_3d_mm": r["median_3d_mm"], "p95_3d_mm": r["p95_3d_mm"], "rmse_3d_mm": r["rmse_3d_mm"], "source": "Phase3 C_Vicon_refit_cm + D_LOO_CV"})
    write_csv(out / "tables/final_oracle_ceiling_comparison.csv", oracle_rows)
    diag_filters = [
        ("L_Vicon", "C_Vicon_cm", "D_LOO_CV"),
        ("L_V4", "C_V4", "D0"),
        ("L_V5", "C_V5", "D_LOO_CV"),
    ]
    diag = []
    for l, c, d in diag_filters:
        g = df[(df["phase"] == "Phase5_transfer") & (df["layout_source"] == l) & (df["correction_source"] == c) & (df["tag_delay_mode"] == d)]
        if not g.empty:
            diag.append(g.iloc[0].to_dict())
    write_csv(out / "tables/final_transfer_matrix_diagonal.csv", diag, UNIFIED_COLUMNS)
    budget = []
    for row in static_cmp:
        budget.append({"row": row["case"], "static_median_3d_mm": row.get("median_3d_mm"), "static_p95_3d_mm": row.get("p95_3d_mm"), "interpretation": row.get("source", "")})
    for row in oracle_rows:
        budget.append({"row": row["case"], "static_median_3d_mm": row.get("median_3d_mm"), "static_p95_3d_mm": row.get("p95_3d_mm"), "interpretation": "oracle ceiling"})
    write_csv(out / "tables/final_error_budget_table.csv", budget)
    report = finish_phase(ctx)
    v5_dloo = next((r for r in static_cmp if r["case"] == "V5 D_LOO_CV"), static_cmp[-1])
    v5_sweep = next((r for r in static_cmp if r["case"] == "V5 D_sweep_opt"), {})
    v5_d0 = next((r for r in static_cmp if r["case"] == "V5 D0"), {})
    transfer_df = df[df["phase"] == "Phase5_transfer"].copy()
    offdiag_best = transfer_df.sort_values(["median_3d_mm", "p95_3d_mm"]).head(8).to_dict("records")
    nlos_path = ANALYSIS / "FULL_V5/tables/per_anchor_nlos_comparison.csv"
    nlos_rows = []
    if nlos_path.exists():
        nlos_df = pd.read_csv(nlos_path)
        nlos_rows = nlos_df.sort_values(["dynamic_positive_spike_rate_gt100", "static_positive_spike_rate_gt100"], ascending=False).head(8).to_dict("records")
    delay_rows = []
    delay_path = ANALYSIS / "FULL_V5/tables/delay_comparison_v4_vs_v5.csv"
    if delay_path.exists():
        delay_rows = pd.read_csv(delay_path).to_dict("records")

    lines = ["# PHASE 6 - V4 vs V5 Final\n\n"]
    lines.append("## Executive Summary\n\n")
    lines.append(f"V5 deployable LOO static is {float(v5_dloo['median_3d_mm']):.1f} mm median / {float(v5_dloo['p95_3d_mm']):.1f} mm P95, slightly better than the V4 production headline while keeping the result deployable. ")
    lines.append(f"The V5 scale problem is effectively fixed: Sim3 scale is {float(phase1_scale[phase1_scale['layout'] == 'v5-commonmode'].iloc[0]['sim3_scale']):.3f}, and rigid anchor RMSE is {float(phase1_scale[phase1_scale['layout'] == 'v5-commonmode'].iloc[0]['rigid_anchor_rmse_mm']):.1f} mm. ")
    lines.append("ROTO remains a best-fit-aligned dynamic floor, so it is useful for relative comparison but not a hardware-synchronized truth claim.\n\n")

    lines.append("## Anchor-Side\n\n")
    lines.append("V5 removes the V4 scale compression: V4 Sim3 scale is about 0.958, while V5 is near unity. Rigid alignment also improves substantially, so V5 should be treated as the better self-calibrated anchor geometry baseline.\n\n")
    append_md_table(lines, phase1_scale.to_dict("records"), ["layout", "sim3_scale", "sim3_anchor_rmse_mm", "rigid_anchor_rmse_mm", "rigid_anchor_median_mm", "rigid_anchor_p95_mm"])
    if delay_rows:
        lines.append("V5 anchor delay uses common-mode plus differential terms; the table below is the direct V4-to-V5 delay transfer comparison.\n\n")
        append_md_table(lines, delay_rows, ["anchor_label", "v4_d_anchor_mm", "v5_d_anchor_mm", "v5_minus_v4_d_anchor_mm", "v5_common_mode_mm", "v5_differential_e_i_mm"])

    lines.append("## Tag-Side\n\n")
    lines.append(f"D0 is clearly not enough for V5 static evaluation: median error is {float(v5_d0.get('median_3d_mm', float('nan'))):.1f} mm. ")
    lines.append(f"The LOO tag delay {49.621:.3f} mm gives the deployable result; the sweep optimum is {float(v5_sweep.get('median_3d_mm', float('nan'))):.1f} mm median but is in-sample and diagnostic only.\n\n")
    append_md_table(lines, static_cmp, ["case", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "source"])

    lines.append("## Oracle Ceiling\n\n")
    lines.append("The known-anchor oracle remains close to the deployed V5 static result. That means most remaining static error is not simply V5 anchor scale; it is tag delay, residual ranging bias, NLOS, and solver/data effects.\n\n")
    append_md_table(lines, oracle_rows, ["case", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "source"])

    lines.append("## Transfer Matrix\n\n")
    lines.append("The diagonal cells separate the three intended operating modes. The best off-diagonal cells show what can be achieved only when correction sources are mixed or D_tag is optimized in-sample.\n\n")
    append_md_table(lines, diag, ["layout_source", "correction_source", "tag_delay_mode", "tag_delay_value_mm", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm"])
    lines.append("Best transfer-matrix cells by median 3D error:\n\n")
    append_md_table(lines, offdiag_best, ["layout_source", "correction_source", "tag_delay_mode", "tag_delay_value_mm", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm"])

    lines.append("## Single-Baseline\n\n")
    lines.append("V5 gets only a limited gain from one external baseline because its scale is already near unity. V4 still shows its known F-H baseline improvement, which is why the single-baseline result is mostly a V4 scale-fix story rather than a V5 requirement.\n\n")
    append_md_table(lines, phase4_cmp.to_dict("records"), ["layout", "best_pair", "best_median_3d_mm", "best_p95_3d_mm", "best_scale_factor"])

    lines.append("## ROTO Dynamic\n\n")
    lines.append("ROTO with V5 LOO improves over V5 D0 and is close to the V4 dynamic floor, but all values remain best-fit aligned with no hardware time sync. Treat this as a motion-floor diagnostic, not absolute synchronized tracking accuracy.\n\n")
    append_md_table(lines, roto_cmp, ["case", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "source"])

    lines.append("## NLOS Fingerprint\n\n")
    lines.append("Static and dynamic rho fingerprints identify which anchors generate positive range spikes. These anchors should be inspected before attributing the remaining dynamic floor to geometry or tag delay alone.\n\n")
    if nlos_rows:
        append_md_table(lines, nlos_rows, ["anchor_label", "static_rho_rms_mm", "dynamic_rho_rms_mm", "static_positive_spike_rate_gt100", "dynamic_positive_spike_rate_gt100", "static_positive_spike_rate_gt150", "dynamic_positive_spike_rate_gt150"])

    lines.append("## Runtime Summary\n\n")
    append_md_table(lines, runtime_rows, ["phase", "elapsed_s", "mean_cpu_percent", "max_cpu_percent", "physical_cores", "logical_cores", "workers"])

    lines.append("## Remaining Gaps / Future Work\n\n")
    lines.append("- Add hardware time synchronization before promoting ROTO from best-fit-aligned diagnostic to absolute dynamic accuracy.\n")
    lines.append("- Keep D_LOO_CV as the deployable tag-delay result; label D_sweep_opt and D_oracle rows as diagnostics only.\n")
    lines.append("- Use the rho/NLOS tables to inspect high-spike anchors and links before claiming the dynamic error floor is solved.\n")
    lines.append("- If more external references are allowed, validate V5 on an independent holdout rather than only the 24-position LOO/sweep setup.\n")
    (out / "reports/PHASE6_V4_VS_V5_FINAL.md").write_text("".join(lines), encoding="utf-8")
    print("".join(lines), flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the V5 full ablation pipeline into six new output directories.")
    parser.add_argument("--replace", action="store_true", help="allow writing into existing output directories")
    args = parser.parse_args()
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    global_start = time.perf_counter()
    print(json.dumps({"stage": "start", "run_id": run_id, "base": str(BASE), "analysis": str(ANALYSIS), "workers": 6, "gpu": "idle_not_used"}, sort_keys=True), flush=True)
    inputs = prepare_inputs()
    dirs = make_dirs()
    runtime_rows: list[dict[str, Any]] = []
    all_unified: list[dict[str, Any]] = []
    p1, fits = run_phase1(dirs, inputs)
    runtime_rows.append(p1)
    p2, u2 = run_phase2(dirs, inputs, fits, run_id)
    runtime_rows.append(p2)
    all_unified.extend(u2)
    p3, u3, cm_delays = run_phase3(dirs, inputs, run_id)
    runtime_rows.append(p3)
    all_unified.extend(u3)
    p4, u4 = run_phase4(dirs, inputs, run_id)
    runtime_rows.append(p4)
    all_unified.extend(u4)
    p5, u5 = run_phase5(dirs, inputs, fits, cm_delays, run_id)
    runtime_rows.append(p5)
    all_unified.extend(u5)
    p6 = run_phase6(dirs, all_unified, runtime_rows, run_id)
    runtime_rows.append(p6)
    total_wall = time.perf_counter() - global_start
    mean_cpu = float(np.nanmean([r["mean_cpu_percent"] for r in runtime_rows]))
    max_cpu = float(np.nanmax([r["max_cpu_percent"] for r in runtime_rows]))
    summary_lines = [
        "=== FULL V5 ABLATION PIPELINE - RUNTIME SUMMARY ===\n",
        "Machine: i7-8700K 6C/12T 32GB\n",
        "Workers: 6 (process pool)\n",
        "GPU: idle (not used)\n\n",
    ]
    labels = [
        ("Phase 1 (scale)", p1),
        ("Phase 2 (FULL_V5)", p2),
        ("Phase 3 (align_to_Vicon)", p3),
        ("Phase 4 (one_baseline)", p4),
        ("Phase 5 (transfer_matrix)", p5),
        ("Phase 6 (final_report)", p6),
    ]
    for label, row in labels:
        summary_lines.append(f"{label:<30} {row['elapsed_s']:.1f} s\n")
    summary_lines.append(f"Total wall time:               {total_wall:.1f} s\n\n")
    summary_lines.append(f"Mean CPU%: {mean_cpu:.1f}%\n")
    summary_lines.append(f"Max CPU%:  {max_cpu:.1f}%\n")
    summary = "".join(summary_lines)
    print(summary, flush=True)
    write_csv(ANALYSIS / "FULL_V4_vs_V5_final/tables/runtime_summary.csv", runtime_rows)
    (ANALYSIS / "FULL_V4_vs_V5_final/reports/RUNTIME_SUMMARY.txt").write_text(summary, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
