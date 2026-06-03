#!/usr/bin/env python3
"""Filtered deployment static-tag matrix.

This script deliberately writes under `filtered_deployment/` and does not
overwrite the unfiltered official validation.  OptiTrack truth is used only for
final evaluation after the anchor-locked transform is fixed from anchors.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ANCHORS = list("ABCDEFGH")
LAYOUT_VERSIONS = ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
TAG_METHODS = ["T1", "T2", "T3", "T4"]
EXTERNAL_FILTERS = ["F0", "F1", "F2", "F3", "F4", "F5"]
T5_VARIANTS = ["T5a", "T5b", "T5c", "T5d", "T5e"]
OPTITRACK_VERTICAL_AXIS = "Y"
THRESHOLDS_MM = [50, 80, 100, 200, 300]

THIS = Path(__file__).resolve()
EXTRA_ROOT = THIS.parents[2]
REPO_ROOT = THIS.parents[7]
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"
sys.path.insert(0, str(EXTRA_ROOT / "scripts"))
sys.path.insert(0, str(SOLVER_ROOT))

from tag_ground_truth import load_corrected_static_truth  # noqa: E402
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json  # noqa: E402
from biospur_tag_positioning_offline_solver.models import Frame, Layout, SolveResult, SolverConfig  # noqa: E402


@dataclass
class FilterPoint:
    tag: str
    sweep: int
    host_elapsed_s: float
    host_epoch_s: float
    xyz_mm: np.ndarray
    residual_rms_mm: float
    anchors_input: int
    anchors_used: int
    rejected_anchor_id: int | None = None
    filter_updates: int = 1
    filter_rejections: int = 0
    filter_kind: str = ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_reflection=True, allow_scale=False):
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(u @ vt) < 0:
        d[-1] = -1
    r = u @ np.diag(d) @ vt
    scale = 1.0
    if allow_scale:
        scale = float(np.sum(s * d) / np.sum(x * x))
    t = dst_c - scale * src_c @ r
    return r, t, scale, float(np.linalg.det(r))


def apply_transform(points: np.ndarray, r: np.ndarray, t: np.ndarray, scale: float) -> np.ndarray:
    return scale * points @ r + t


def load_autopos_layout_coords(path: Path) -> tuple[list[str], np.ndarray]:
    data = json.loads(path.read_text(encoding="utf-8"))
    anchors = data["anchors"]
    labels = [a.get("label", chr(ord("A") + int(a["id"]))) for a in anchors]
    coords = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in anchors], dtype=float)
    return labels, coords


def session_id_from_path(path: Path) -> str:
    for parent in path.parents:
        if parent.name.startswith("static_ID"):
            return parent.name.split("_")[1]
    return path.parents[1].name


def capture_name_from_path(path: Path) -> str:
    for parent in path.parents:
        if parent.name.startswith("static_ID"):
            return parent.name
    return path.parents[1].name


def load_static_metadata(layout_table: Path) -> dict[str, dict]:
    if not layout_table.exists():
        return {}
    df = pd.read_csv(layout_table)
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


def load_truth(opti_dir: Path):
    primary = ["ID01", "ID02", "ID03", "ID04", "ID05"]
    return load_corrected_static_truth(opti_dir, ANCHORS, primary)


def filter_frames(frames: list[Frame], allowed_anchor_ids: set[int], min_anchors: int) -> list[Frame]:
    out: list[Frame] = []
    for frame in frames:
        obs = tuple(o for o in frame.observations if o.anchor_id in allowed_anchor_ids)
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


def solve_frames(layout: Layout, method: str, frames: list[Frame]) -> list[SolveResult]:
    solver = TagPositionSolver(layout, SolverConfig(method=method))  # type: ignore[arg-type]
    results = []
    for frame in frames:
        result = solver.solve_frame(frame)
        if result is not None:
            results.append(result)
    return results


def solve_one_t1(layout: Layout, frame: Frame) -> SolveResult | None:
    solver = TagPositionSolver(layout, SolverConfig(method="T1"))
    return solver.solve_frame(frame)


def result_to_point(result: SolveResult, filter_kind: str) -> FilterPoint:
    return FilterPoint(
        tag=result.tag,
        sweep=result.sweep,
        host_elapsed_s=result.host_elapsed_s,
        host_epoch_s=result.host_epoch_s,
        xyz_mm=np.array([result.x_mm, result.y_mm, result.z_mm], dtype=float),
        residual_rms_mm=float(result.residual_rms_mm),
        anchors_input=int(result.anchors_input),
        anchors_used=int(result.anchors_used),
        rejected_anchor_id=result.rejected_anchor_id,
        filter_kind=filter_kind,
    )


def stable_dt(prev_t: float, cur_t: float) -> float:
    dt = float(cur_t - prev_t)
    if not math.isfinite(dt) or dt <= 0.0:
        return 0.1
    return min(max(dt, 0.005), 1.0)


def cv_predict(x: np.ndarray, p: np.ndarray, dt: float, accel_sigma: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(x)
    f = np.eye(n)
    f[0, 3] = dt
    f[1, 4] = dt
    f[2, 5] = dt
    q = np.zeros((n, n), dtype=float)
    s2 = accel_sigma * accel_sigma
    q_pos = 0.25 * dt**4 * s2
    q_cross = 0.5 * dt**3 * s2
    q_vel = dt**2 * s2
    for axis in range(3):
        q[axis, axis] = q_pos
        q[axis, axis + 3] = q_cross
        q[axis + 3, axis] = q_cross
        q[axis + 3, axis + 3] = q_vel
    if n == 7:
        q[6, 6] = (5.0 * dt) ** 2
    xp = f @ x
    pp = f @ p @ f.T + q
    return xp, pp, f, q


def position_kf(results: list[SolveResult], variant: str) -> list[FilterPoint]:
    if variant == "F0":
        return [result_to_point(r, "F0") for r in results]
    if not results:
        return []

    meas = [np.array([r.x_mm, r.y_mm, r.z_mm], dtype=float) for r in results]
    x = np.zeros(6, dtype=float)
    x[:3] = meas[0]
    p = np.diag([120.0**2, 120.0**2, 160.0**2, 500.0**2, 500.0**2, 600.0**2])
    h = np.zeros((3, 6), dtype=float)
    h[:, :3] = np.eye(3)

    xs_f: list[np.ndarray] = []
    ps_f: list[np.ndarray] = []
    xs_pred: list[np.ndarray] = []
    ps_pred: list[np.ndarray] = []
    fs: list[np.ndarray] = []
    points: list[FilterPoint] = []
    prev_t = results[0].host_epoch_s
    prev_meas = meas[0]
    rejections_total = 0

    for idx, (result, z) in enumerate(zip(results, meas)):
        dt = stable_dt(prev_t, result.host_epoch_s) if idx > 0 else 0.1
        accel = 650.0
        if variant == "F3" and idx > 0:
            speed = float(np.linalg.norm(z - prev_meas) / max(dt, 1e-3))
            accel = 2000.0 if speed > 500.0 else 300.0
        x_pred, p_pred, f, _q = cv_predict(x, p, dt, accel)
        residual_sigma = max(45.0, min(250.0, result.residual_rms_mm if math.isfinite(result.residual_rms_mm) else 80.0))
        r_scale = 1.0
        innov = z - h @ x_pred
        nis_like = float(innov.T @ innov) / max(residual_sigma * residual_sigma, 1.0)
        rejected = 0
        if variant in {"F2", "F3", "F4"} and nis_like > 25.0:
            r_scale = min(25.0, nis_like / 9.0)
            rejected = 1
            rejections_total += 1
        rmat = np.diag([residual_sigma**2, residual_sigma**2, (1.25 * residual_sigma) ** 2]) * r_scale
        s = h @ p_pred @ h.T + rmat
        k = p_pred @ h.T @ np.linalg.pinv(s)
        x = x_pred + k @ innov
        i_kh = np.eye(6) - k @ h
        p = i_kh @ p_pred @ i_kh.T + k @ rmat @ k.T
        xs_pred.append(x_pred.copy())
        ps_pred.append(p_pred.copy())
        fs.append(f.copy())
        xs_f.append(x.copy())
        ps_f.append(p.copy())
        points.append(
            FilterPoint(
                tag=result.tag,
                sweep=result.sweep,
                host_elapsed_s=result.host_elapsed_s,
                host_epoch_s=result.host_epoch_s,
                xyz_mm=x[:3].copy(),
                residual_rms_mm=result.residual_rms_mm,
                anchors_input=result.anchors_input,
                anchors_used=result.anchors_used,
                rejected_anchor_id=result.rejected_anchor_id,
                filter_updates=1,
                filter_rejections=rejected,
                filter_kind=variant,
            )
        )
        prev_t = result.host_epoch_s
        prev_meas = z

    if variant == "F4" and len(points) >= 3:
        # Fixed-lag deployment smoother: adds bounded output latency but avoids
        # full-sequence future information.  For the static report the delayed
        # samples are evaluated at the original capture timestamps.
        lag = 8
        arr = np.array([pnt.xyz_mm for pnt in points], dtype=float)
        for i, point in enumerate(points):
            lo = max(0, i - lag)
            hi = min(len(points), i + lag + 1)
            idx = np.arange(lo, hi)
            weights = 1.0 / (1.0 + np.abs(idx - i).astype(float))
            point.xyz_mm = np.sum(arr[lo:hi] * weights[:, None], axis=0) / np.sum(weights)
            point.filter_kind = "F4_FIXED_LAG"
        return points

    if variant != "F5" or len(points) < 3:
        return points

    smooth = [arr.copy() for arr in xs_f]
    smooth_p = [arr.copy() for arr in ps_f]
    for i in range(len(points) - 2, -1, -1):
        c = ps_f[i] @ fs[i + 1].T @ np.linalg.pinv(ps_pred[i + 1])
        smooth[i] = xs_f[i] + c @ (smooth[i + 1] - xs_pred[i + 1])
        smooth_p[i] = ps_f[i] + c @ (smooth_p[i + 1] - ps_pred[i + 1]) @ c.T
    for point, sstate in zip(points, smooth):
        point.xyz_mm = sstate[:3].copy()
        point.filter_kind = "F5_RTS_OFFLINE"
    return points


def range_obs_arrays(layout: Layout, frame: Frame):
    aids = []
    anchors = []
    ranges = []
    sigmas = []
    qualities = []
    delays = []
    for obs in frame.observations:
        anchor = layout.anchors.get(obs.anchor_id)
        if anchor is None or obs.range_mm <= 0.0:
            continue
        aids.append(obs.anchor_id)
        anchors.append([anchor.x_mm, anchor.y_mm, anchor.z_mm])
        ranges.append(obs.range_mm)
        sigmas.append(max(20.0, anchor.sigma_mm))
        q = obs.quality_percent if math.isfinite(obs.quality_percent) and obs.quality_percent > 0 else 100.0
        qualities.append(q)
        delays.append(anchor.d_anchor_mm)
    return (
        np.array(aids, dtype=int),
        np.array(anchors, dtype=float),
        np.array(ranges, dtype=float),
        np.array(sigmas, dtype=float),
        np.array(qualities, dtype=float),
        np.array(delays, dtype=float),
    )


def effective_range_sigma(sigmas: np.ndarray, qualities: np.ndarray) -> np.ndarray:
    q_scale = np.sqrt(np.clip(100.0 / np.maximum(qualities, 10.0), 1.0, 10.0))
    return np.maximum(25.0, sigmas * q_scale)


def ekf_range_update(x: np.ndarray, p: np.ndarray, layout: Layout, frame: Frame, variant: str) -> tuple[np.ndarray, np.ndarray, float, int, int]:
    _aids, anchors, ranges, sigmas, qualities, delays = range_obs_arrays(layout, frame)
    n_obs = len(ranges)
    if n_obs < 4:
        return x, p, float("nan"), 0, 0
    pos = x[:3]
    vec = pos[None, :] - anchors
    dist = np.linalg.norm(vec, axis=1)
    dist = np.maximum(dist, 1.0)
    pred = dist + delays + layout.tag_delay_mm
    if len(x) == 7:
        pred = pred + x[6]
    y = ranges - pred
    unit = vec / dist[:, None]
    if variant in {"T5b", "T5c"}:
        sigma = effective_range_sigma(sigmas, qualities)
        keep = np.abs(y) < np.maximum(350.0, 5.0 * sigma)
        if keep.sum() >= 4:
            anchors = anchors[keep]
            ranges = ranges[keep]
            delays = delays[keep]
            sigma = sigma[keep]
            y = y[keep]
            unit = unit[keep]
        huber_k = 2.5
        scale = np.maximum(1.0, np.abs(y) / np.maximum(huber_k * sigma, 1.0))
        sigma = sigma * np.sqrt(scale)
    else:
        sigma = effective_range_sigma(sigmas, qualities)
    m = len(y)
    h = np.zeros((m, len(x)), dtype=float)
    h[:, :3] = unit
    if len(x) == 7:
        h[:, 6] = 1.0
    rmat = np.diag(sigma * sigma)
    s = h @ p @ h.T + rmat
    k = p @ h.T @ np.linalg.pinv(s)
    x_new = x + k @ y
    i_kh = np.eye(len(x)) - k @ h
    p_new = i_kh @ p @ i_kh.T + k @ rmat @ k.T
    rms = float(np.sqrt(np.nanmean(y * y)))
    return x_new, p_new, rms, m, n_obs - m


def run_range_ekf(layout: Layout, frames: list[Frame], variant: str) -> list[FilterPoint]:
    if not frames:
        return []
    first = None
    first_index = 0
    for i, frame in enumerate(frames):
        first = solve_one_t1(layout, frame)
        if first is not None:
            first_index = i
            break
    if first is None:
        return []
    dim = 7 if variant == "T5e" else 6
    x = np.zeros(dim, dtype=float)
    x[:3] = np.array([first.x_mm, first.y_mm, first.z_mm], dtype=float)
    p_diag = [250.0**2, 250.0**2, 300.0**2, 1000.0**2, 1000.0**2, 1000.0**2]
    if dim == 7:
        p_diag.append(150.0**2)
    p = np.diag(p_diag)
    points: list[FilterPoint] = []
    prev_t = frames[first_index].host_epoch_s
    for frame in frames[first_index:]:
        dt = stable_dt(prev_t, frame.host_epoch_s)
        accel = 800.0
        if variant == "T5c" and frame.imu is not None and frame.imu.valid and frame.imu.acc_norm_std_mg is not None:
            accel = 300.0 if frame.imu.acc_norm_std_mg < 15.0 else 1800.0
        x, p, _f, _q = cv_predict(x, p, dt, accel)
        x, p, rms, used, rejected = ekf_range_update(x, p, layout, frame, variant)
        if used >= 4:
            points.append(
                FilterPoint(
                    tag=frame.tag,
                    sweep=frame.sweep,
                    host_elapsed_s=frame.host_elapsed_s,
                    host_epoch_s=frame.host_epoch_s,
                    xyz_mm=x[:3].copy(),
                    residual_rms_mm=rms,
                    anchors_input=len(frame.observations),
                    anchors_used=used,
                    filter_updates=1,
                    filter_rejections=rejected,
                    filter_kind=variant,
                )
            )
        prev_t = frame.host_epoch_s
    return points


def run_range_ukf(layout: Layout, frames: list[Frame]) -> list[FilterPoint]:
    if not frames:
        return []
    first = None
    first_index = 0
    for i, frame in enumerate(frames):
        first = solve_one_t1(layout, frame)
        if first is not None:
            first_index = i
            break
    if first is None:
        return []
    n = 6
    alpha = 0.25
    beta = 2.0
    kappa = 0.0
    lam = alpha * alpha * (n + kappa) - n
    wm = np.full(2 * n + 1, 1.0 / (2.0 * (n + lam)))
    wc = wm.copy()
    wm[0] = lam / (n + lam)
    wc[0] = wm[0] + (1.0 - alpha * alpha + beta)
    x = np.zeros(n, dtype=float)
    x[:3] = np.array([first.x_mm, first.y_mm, first.z_mm], dtype=float)
    p = np.diag([250.0**2, 250.0**2, 300.0**2, 1000.0**2, 1000.0**2, 1000.0**2])
    prev_t = frames[first_index].host_epoch_s
    points: list[FilterPoint] = []

    for frame in frames[first_index:]:
        dt = stable_dt(prev_t, frame.host_epoch_s)
        x, p, _f, q = cv_predict(x, p, dt, 800.0)
        aids, anchors, ranges, sigmas, qualities, delays = range_obs_arrays(layout, frame)
        if len(ranges) < 4:
            continue
        jitter = 1e-6 * np.eye(n)
        try:
            chol = np.linalg.cholesky((n + lam) * (p + jitter))
        except np.linalg.LinAlgError:
            chol = np.linalg.cholesky((n + lam) * (p + 1e-2 * np.eye(n)))
        sigma_pts = [x]
        for j in range(n):
            sigma_pts.append(x + chol[:, j])
            sigma_pts.append(x - chol[:, j])
        sigma_pts = np.array(sigma_pts)
        z_sigma = []
        for sp in sigma_pts:
            dist = np.linalg.norm(sp[:3][None, :] - anchors, axis=1)
            z_sigma.append(dist + delays + layout.tag_delay_mm)
        z_sigma = np.array(z_sigma)
        z_pred = wm @ z_sigma
        dz = z_sigma - z_pred[None, :]
        dx = sigma_pts - x[None, :]
        sigma = effective_range_sigma(sigmas, qualities)
        s = dz.T @ (wc[:, None] * dz) + np.diag(sigma * sigma)
        pxz = dx.T @ (wc[:, None] * dz)
        k = pxz @ np.linalg.pinv(s)
        y = ranges - z_pred
        x = x + k @ y
        p = p - k @ s @ k.T + q * 0.0
        rms = float(np.sqrt(np.nanmean(y * y)))
        points.append(
            FilterPoint(
                tag=frame.tag,
                sweep=frame.sweep,
                host_elapsed_s=frame.host_elapsed_s,
                host_epoch_s=frame.host_epoch_s,
                xyz_mm=x[:3].copy(),
                residual_rms_mm=rms,
                anchors_input=len(frame.observations),
                anchors_used=len(ranges),
                filter_kind="T5d",
            )
        )
        prev_t = frame.host_epoch_s
    return points


def summarize_points(points: list[FilterPoint], point_estimator: str) -> dict:
    if not points:
        return {
            "status": "no_solution",
            "frames_solved": 0,
            "x_mm": float("nan"),
            "y_mm": float("nan"),
            "z_mm": float("nan"),
        }
    pts = np.array([p.xyz_mm for p in points], dtype=float)
    if point_estimator == "mean":
        center = np.nanmean(pts, axis=0)
    else:
        center = np.nanmedian(pts, axis=0)
    d = pts - center[None, :]
    d3 = np.linalg.norm(d, axis=1)
    residual = np.array([p.residual_rms_mm for p in points], dtype=float)
    anchors_used = np.array([p.anchors_used for p in points], dtype=float)
    anchors_input = np.array([p.anchors_input for p in points], dtype=float)
    return {
        "status": "ok",
        "frames_solved": int(len(points)),
        "x_mm": float(center[0]),
        "y_mm": float(center[1]),
        "z_mm": float(center[2]),
        "mean_x_mm": float(np.nanmean(pts[:, 0])),
        "mean_y_mm": float(np.nanmean(pts[:, 1])),
        "mean_z_mm": float(np.nanmean(pts[:, 2])),
        "median_x_mm": float(np.nanmedian(pts[:, 0])),
        "median_y_mm": float(np.nanmedian(pts[:, 1])),
        "median_z_mm": float(np.nanmedian(pts[:, 2])),
        "x_std_mm": float(np.nanstd(d[:, 0])),
        "y_std_mm": float(np.nanstd(d[:, 1])),
        "z_std_mm": float(np.nanstd(d[:, 2])),
        "d3_std_mm": float(np.sqrt(np.nanmean(d3 * d3))),
        "radial_p50_mm": float(np.nanpercentile(d3, 50)),
        "radial_p95_mm": float(np.nanpercentile(d3, 95)),
        "anchors_used_median": float(np.nanmedian(anchors_used)),
        "anchors_input_median": float(np.nanmedian(anchors_input)),
        "pct_solved_ge7": float(np.mean(anchors_input >= 7.0) * 100.0),
        "pct_solved_ge8": float(np.mean(anchors_input >= 8.0) * 100.0),
        "residual_rms_median_mm": float(np.nanmedian(residual)),
        "residual_rms_p95_mm": float(np.nanpercentile(residual, 95)),
        "filter_rejections": int(sum(p.filter_rejections for p in points)),
    }


def summarize_abs(rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    out: list[dict] = []
    for (version, solver, family, eval_set), g in df.groupby(["version", "solver", "solver_family", "eval_set"]):
        err = g["err_3d_mm"].to_numpy(dtype=float)
        out.append(
            {
                "version": version,
                "solver": solver,
                "solver_family": family,
                "eval_set": eval_set,
                "n_sessions": int(len(g)),
                "frames_solved_total": int(g["frames_solved"].sum()),
                "frames_input_total": int(g["frames_input"].sum()),
                "err_3d_mean_mm": float(np.nanmean(err)),
                "err_3d_median_mm": float(np.nanmedian(err)),
                "err_3d_p75_mm": float(np.nanpercentile(err, 75)),
                "err_3d_p90_mm": float(np.nanpercentile(err, 90)),
                "err_3d_p95_mm": float(np.nanpercentile(err, 95)),
                "err_3d_p99_mm": float(np.nanpercentile(err, 99)),
                "err_3d_max_mm": float(np.nanmax(err)),
                "err_3d_rms_mm": float(np.sqrt(np.nanmean(err * err))),
                "err_horizontal_median_mm": float(np.nanmedian(g["err_horizontal_mm"].to_numpy(dtype=float))),
                "err_vertical_median_mm": float(np.nanmedian(g["err_vertical_mm"].to_numpy(dtype=float))),
                "d3_std_median_mm": float(np.nanmedian(g["d3_std_mm"].to_numpy(dtype=float))),
                "radial_p95_median_mm": float(np.nanmedian(g["radial_p95_mm"].to_numpy(dtype=float))),
                "residual_rms_median_mm": float(np.nanmedian(g["residual_rms_median_mm"].to_numpy(dtype=float))),
                "filter_rejections_total": int(g["filter_rejections"].sum()),
            }
        )
    return sorted(out, key=lambda r: (r["eval_set"], r["version"], r["solver"]))


def build_metrics_tables(session_rows: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict], list[dict]]:
    df = pd.DataFrame(session_rows)
    metric_rows: list[dict] = []
    axis_rows: list[dict] = []
    outlier_rows: list[dict] = []
    strat_height: list[dict] = []
    strat_edge: list[dict] = []
    strat_facing: list[dict] = []
    if df.empty:
        return metric_rows, axis_rows, outlier_rows, strat_height, strat_edge, strat_facing
    group_cols = ["version", "solver", "solver_family", "eval_set"]
    for key, g in df.groupby(group_cols):
        version, solver, family, eval_set = key
        err = g["err_3d_mm"].to_numpy(dtype=float)
        base = {
            "version": version,
            "solver": solver,
            "solver_family": family,
            "eval_set": eval_set,
            "n_positions": int(len(g)),
        }
        for metric, value in [
            ("mean_3d_error_mm", np.nanmean(err)),
            ("rmse_3d_error_mm", np.sqrt(np.nanmean(err * err))),
            ("p50_3d_error_mm", np.nanpercentile(err, 50)),
            ("p90_3d_error_mm", np.nanpercentile(err, 90)),
            ("p95_3d_error_mm", np.nanpercentile(err, 95)),
            ("p99_3d_error_mm", np.nanpercentile(err, 99)),
            ("max_3d_error_mm", np.nanmax(err)),
            ("repeatability_d3_std_p50_mm", np.nanmedian(g["d3_std_mm"].to_numpy(dtype=float))),
            ("repeatability_d3_std_p95_mm", np.nanpercentile(g["d3_std_mm"].to_numpy(dtype=float), 95)),
        ]:
            metric_rows.append({**base, "metric": metric, "value_mm": float(value), "unit": "mm"})
        for component, col, role in [
            ("X", "err_x_mm", "OptiTrack horizontal X"),
            ("Y_vertical", "err_y_vertical_mm", "OptiTrack vertical Y"),
            ("Z", "err_z_mm", "OptiTrack horizontal Z"),
        ]:
            signed = g[col].to_numpy(dtype=float)
            axis_rows.append(
                {
                    **base,
                    "component": component,
                    "role": role,
                    "signed_mean_bias_mm": float(np.nanmean(signed)),
                    "signed_std_mm": float(np.nanstd(signed, ddof=1)),
                    "p95_abs_error_mm": float(np.nanpercentile(np.abs(signed), 95)),
                    "axis_convention": "OptiTrack frame; Y is vertical",
                }
            )
        horiz = g["err_horizontal_mm"].to_numpy(dtype=float)
        axis_rows.append(
            {
                **base,
                "component": "horizontal_XZ_2D",
                "role": "secondary horizontal 2D",
                "signed_mean_bias_mm": "",
                "signed_std_mm": "",
                "p95_abs_error_mm": "",
                "horizontal_2d_rmse_mm": float(np.sqrt(np.nanmean(horiz * horiz))),
                "horizontal_2d_p50_mm": float(np.nanpercentile(horiz, 50)),
                "horizontal_2d_p95_mm": float(np.nanpercentile(horiz, 95)),
                "axis_convention": "OptiTrack frame; Y is vertical",
            }
        )
        for th in THRESHOLDS_MM:
            outlier_rows.append(
                {
                    **base,
                    "threshold_mm": th,
                    "within_count": int(np.sum(err <= th)),
                    "within_fraction": float(np.mean(err <= th)),
                    "outside_count": int(np.sum(err > th)),
                    "outside_fraction": float(np.mean(err > th)),
                }
            )
        for col, target in [("height", strat_height), ("location", strat_edge), ("facing", strat_facing)]:
            for label, sg in g.groupby(col):
                serr = sg["err_3d_mm"].to_numpy(dtype=float)
                target.append(
                    {
                        **base,
                        col: label,
                        "n": int(len(sg)),
                        "median_3d_mm": float(np.nanmedian(serr)),
                        "p95_3d_mm": float(np.nanpercentile(serr, 95)),
                        "rms_3d_mm": float(np.sqrt(np.nanmean(serr * serr))),
                    }
                )
    return metric_rows, axis_rows, outlier_rows, strat_height, strat_edge, strat_facing


def radial_diagnostics(session_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    df = pd.DataFrame(session_rows)
    detail: list[dict] = []
    summary: list[dict] = []
    if df.empty:
        return detail, summary
    for key, g in df.groupby(["version", "solver", "solver_family", "eval_set"]):
        version, solver, family, eval_set = key
        center = g[["truth_x_mm", "truth_y_vertical_mm", "truth_z_mm"]].to_numpy(dtype=float).mean(axis=0)
        vectors = g[["err_x_mm", "err_y_vertical_mm", "err_z_mm"]].to_numpy(dtype=float)
        truths = g[["truth_x_mm", "truth_y_vertical_mm", "truth_z_mm"]].to_numpy(dtype=float)
        radial_dirs = truths - center[None, :]
        radial_norm = np.linalg.norm(radial_dirs, axis=1)
        radial_unit = radial_dirs / np.maximum(radial_norm[:, None], 1e-9)
        radial_signed = np.sum(vectors * radial_unit, axis=1)
        tangential = np.sqrt(np.maximum(0.0, np.sum(vectors * vectors, axis=1) - radial_signed * radial_signed))
        for idx, (_, row) in enumerate(g.iterrows()):
            detail.append(
                {
                    "version": version,
                    "solver": solver,
                    "solver_family": family,
                    "eval_set": eval_set,
                    "ID": row["ID"],
                    "center_distance_mm": float(radial_norm[idx]),
                    "radial_signed_error_mm": float(radial_signed[idx]),
                    "tangential_error_mm": float(tangential[idx]),
                    "radially_outward": bool(radial_signed[idx] > 0),
                }
            )
        if len(radial_norm) >= 3:
            slope, intercept = np.polyfit(radial_norm / 1000.0, radial_signed, 1)
            pred = slope * (radial_norm / 1000.0) + intercept
            ss_res = float(np.sum((radial_signed - pred) ** 2))
            ss_tot = float(np.sum((radial_signed - np.mean(radial_signed)) ** 2))
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        else:
            slope = intercept = r2 = float("nan")
        summary.append(
            {
                "version": version,
                "solver": solver,
                "solver_family": family,
                "eval_set": eval_set,
                "median_radial_signed_mm": float(np.nanmedian(radial_signed)),
                "median_tangential_mm": float(np.nanmedian(tangential)),
                "radially_outward_fraction": float(np.mean(radial_signed > 0)),
                "radial_slope_mm_per_m": float(slope),
                "radial_r2": float(r2),
            }
        )
    return detail, summary


def plot_summary(summary_rows: list[dict], session_rows: list[dict], figs_dir: Path) -> None:
    figs_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(summary_rows)
    ses = pd.DataFrame(session_rows)
    if df.empty or ses.empty:
        return
    v4 = df[(df["version"] == "v4-io") & (df["eval_set"] == "all8")].sort_values("err_3d_median_mm")
    fig, ax = plt.subplots(figsize=(12, 6), constrained_layout=True)
    labels = v4["solver"].tolist()
    ax.bar(np.arange(len(v4)), v4["err_3d_median_mm"], label="median")
    ax.scatter(np.arange(len(v4)), v4["err_3d_p95_mm"], color="crimson", s=24, label="p95")
    ax.set_xticks(np.arange(len(v4)))
    ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("3D absolute error mm")
    ax.set_title("Filtered deployment static matrix: v4-io/all8")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.savefig(figs_dir / "filtered_v4io_all8_solver_ranking.png", dpi=150)
    plt.close(fig)

    selected = ["T4+F0", "T4+F1", "T4+F2", "T4+F3", "T4+F5", "T5a", "T5b", "T5c", "T5d", "T5e"]
    fig, ax = plt.subplots(figsize=(7, 5), constrained_layout=True)
    for solver in selected:
        sub = ses[(ses["version"] == "v4-io") & (ses["eval_set"] == "all8") & (ses["solver"] == solver)]
        if len(sub):
            vals = np.sort(sub["err_3d_mm"].to_numpy(dtype=float))
            y = np.arange(1, len(vals) + 1) / len(vals)
            ax.plot(vals, y, label=solver)
    ax.set_xlabel("3D absolute error mm")
    ax.set_ylabel("CDF")
    ax.set_title("Filtered deployment CDF, v4-io/all8")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)
    fig.savefig(figs_dir / "filtered_static_cdf_v4io_all8.png", dpi=150)
    plt.close(fig)

    ids = sorted(ses["ID"].unique())
    fig, ax = plt.subplots(figsize=(13, 5), constrained_layout=True)
    x = np.arange(len(ids))
    width = 0.12
    by_pos = ["T4+F0", "T4+F1", "T5a", "T5b", "T5c"]
    for k, solver in enumerate(by_pos):
        vals = []
        for sid in ids:
            sub = ses[(ses["version"] == "v4-io") & (ses["eval_set"] == "all8") & (ses["solver"] == solver) & (ses["ID"] == sid)]
            vals.append(float(sub["err_3d_mm"].iloc[0]) if len(sub) else np.nan)
        ax.bar(x + (k - 2) * width, vals, width=width, label=solver)
    ax.set_xticks(x)
    ax.set_xticklabels(ids, rotation=45, ha="right")
    ax.set_ylabel("3D absolute error mm")
    ax.set_title("Filtered deployment by static position, v4-io/all8")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(fontsize=8)
    fig.savefig(figs_dir / "filtered_vs_unfiltered_by_position_v4io_all8.png", dpi=150)
    plt.close(fig)


def write_report(path: Path, summary_rows: list[dict]) -> None:
    df = pd.DataFrame(summary_rows)
    lines = ["# Filtered Deployment Static Results\n\n"]
    lines.append("These results are deployment-output metrics. They do not replace the official unfiltered calibration/measurement validation.\n\n")
    lines.append("OptiTrack truth was used only after solving/filtering, for final evaluation under the anchor-locked transform.\n\n")
    if df.empty:
        lines.append("No rows generated.\n")
        path.write_text("".join(lines), encoding="utf-8")
        return
    v4 = df[(df["version"] == "v4-io") & (df["eval_set"] == "all8")].sort_values("err_3d_median_mm")
    lines.append("## V4-io / all8 Ranking\n\n")
    lines.append("| rank | solver | family | median 3D | p95 3D | RMS 3D | repeat D3 std median |\n")
    lines.append("| ---: | --- | --- | ---: | ---: | ---: | ---: |\n")
    for rank, (_, row) in enumerate(v4.iterrows(), start=1):
        lines.append(
            f"| {rank} | {row['solver']} | {row['solver_family']} | "
            f"{row['err_3d_median_mm']:.1f} | {row['err_3d_p95_mm']:.1f} | "
            f"{row['err_3d_rms_mm']:.1f} | {row['d3_std_median_mm']:.1f} |\n"
        )
    lines.append("\n## Full Summary\n\n")
    lines.append("| version | solver | eval | median 3D | p95 3D | RMS 3D | h.med | v.med | repeat D3 |\n")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")
    for _, row in df.sort_values(["eval_set", "version", "err_3d_median_mm"]).iterrows():
        lines.append(
            f"| {row['version']} | {row['solver']} | {row['eval_set']} | "
            f"{row['err_3d_median_mm']:.1f} | {row['err_3d_p95_mm']:.1f} | "
            f"{row['err_3d_rms_mm']:.1f} | {row['err_horizontal_median_mm']:.1f} | "
            f"{row['err_vertical_median_mm']:.1f} | {row['d3_std_median_mm']:.1f} |\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run filtered static deployment matrix.")
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--layout-versions", default="all")
    parser.add_argument("--tag-methods", default="all")
    parser.add_argument("--external-filters", default="all")
    parser.add_argument("--t5-variants", default="all")
    parser.add_argument("--eval-sets", default="all8")
    parser.add_argument("--point-estimator", choices=["median", "mean"], default="median")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--shard-id", type=int, default=0)
    parser.add_argument(
        "--combine-shards-only",
        action="store_true",
        help="combine existing shard per-session CSVs and write final unsuffixed outputs",
    )
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path(__file__).resolve().parents[1]
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    reports_dir = out_dir / "reports"
    for d in [tables_dir, figs_dir, reports_dir]:
        d.mkdir(parents=True, exist_ok=True)

    layout_base = official_root / "solver/outputs/v1_to_v4_io_field_check"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    captures_root = official_root / "captures/erlangen_20260528_optitrack"
    opti_dir = official_root / "opti_captures/full"
    metadata = load_static_metadata(layout_base / "tables/static_all_captures.csv")
    anchor_truth, tag_truth, tag_truth_meta, _correction_rows = load_truth(opti_dir)
    static_files = sorted(captures_root.glob("static_ID*/tag_capture*/tr_all.csv"))
    if args.layout_versions.lower() == "all":
        layout_versions = LAYOUT_VERSIONS
    else:
        layout_versions = [v.strip() for v in args.layout_versions.split(",") if v.strip()]
    if args.tag_methods.lower() == "all":
        tag_methods = TAG_METHODS
    else:
        tag_methods = [v.strip().upper() for v in args.tag_methods.split(",") if v.strip()]
    if args.external_filters.lower() == "all":
        external_filters = EXTERNAL_FILTERS
    else:
        external_filters = [v.strip().upper() for v in args.external_filters.split(",") if v.strip()]
    if args.t5_variants.lower() == "all":
        t5_variants = T5_VARIANTS
    else:
        t5_variants = [v.strip() for v in args.t5_variants.split(",") if v.strip()]
    eval_sets = [v.strip() for v in args.eval_sets.split(",") if v.strip()]
    allowed_by_eval = {"all8": set(range(8))}

    if args.combine_shards_only:
        shard_paths = sorted(tables_dir.glob("filtered_static_abs_errors_per_session_shard*_of_*.csv"))
        if not shard_paths:
            raise FileNotFoundError(f"no filtered shard per-session CSVs under {tables_dir}")
        frames = [pd.read_csv(path) for path in shard_paths if path.stat().st_size > 0]
        if not frames:
            raise RuntimeError("all filtered shard CSVs are empty")
        session_rows = pd.concat(frames, ignore_index=True).to_dict("records")
        summary_rows = summarize_abs(session_rows)
        metric_rows, axis_rows, outlier_rows, by_height, edge_rows, facing_rows = build_metrics_tables(session_rows)
        radial_detail, radial_summary = radial_diagnostics(session_rows)

        write_csv(tables_dir / "filtered_static_abs_errors_per_session.csv", session_rows)
        write_csv(tables_dir / "filtered_static_accuracy_summary.csv", summary_rows)
        write_csv(tables_dir / "filtered_static_metrics_full.csv", metric_rows)
        write_csv(tables_dir / "filtered_static_per_axis_bias.csv", axis_rows)
        write_csv(tables_dir / "filtered_static_outlier_rates.csv", outlier_rows)
        write_csv(tables_dir / "filtered_static_error_by_height.csv", by_height)
        write_csv(tables_dir / "filtered_static_error_edge_vs_center.csv", edge_rows)
        write_csv(tables_dir / "filtered_static_error_by_facing.csv", facing_rows)
        write_csv(tables_dir / "filtered_static_radial_decomposition.csv", radial_detail)
        write_csv(tables_dir / "filtered_static_radial_summary.csv", radial_summary)
        plot_summary(summary_rows, session_rows, figs_dir)
        write_report(reports_dir / "filtered_static_results.md", summary_rows)

        meta = {
            "script": "filtered_deployment/scripts/run_filtered_static_matrix.py",
            "mode": "combine_shards_only",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "official_root": str(official_root),
            "layout_versions": layout_versions,
            "tag_methods": tag_methods,
            "external_filters": external_filters,
            "t5_variants": t5_variants,
            "eval_sets": eval_sets,
            "max_frames": args.max_frames,
            "truth_usage": "OptiTrack used only for final evaluation; not used in filters.",
            "axis_convention": "OptiTrack Y is vertical",
            "shards": [str(path) for path in shard_paths],
            "outputs": {
                "per_session": str(tables_dir / "filtered_static_abs_errors_per_session.csv"),
                "summary": str(tables_dir / "filtered_static_accuracy_summary.csv"),
                "report": str(reports_dir / "filtered_static_results.md"),
            },
        }
        (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            f"[filtered-static] combined shards={len(shard_paths)} rows={len(session_rows)} "
            f"summary_rows={len(summary_rows)}",
            flush=True,
        )
        return 0

    print(f"[filtered-static] loading {len(static_files)} static captures", flush=True)
    raw_frames: dict[str, list[Frame]] = {}
    for path in static_files:
        frames = read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
        if args.max_frames > 0:
            frames = frames[: args.max_frames]
        raw_frames[str(path)] = frames

    session_rows: list[dict] = []
    t_start = time.perf_counter()
    total_blocks = len(layout_versions) * len(eval_sets)
    block = 0
    for version in layout_versions:
        layout_path = layout_base / version / "layout.json"
        layout = load_layout_json(layout_path, sigma_path)
        labels, coords = load_autopos_layout_coords(layout_path)
        for eval_set in eval_sets:
            block_idx = block
            block += 1
            if block_idx % args.num_shards != args.shard_id:
                continue
            allowed = allowed_by_eval[eval_set]
            anchor_labels = [ANCHORS[i] for i in sorted(allowed)]
            idx = [labels.index(a) for a in anchor_labels]
            src = coords[idx]
            dst = np.array([anchor_truth[a] for a in anchor_labels], dtype=float)
            r, t, scale, det = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
            _, _, scale_diag, _ = fit_similarity(src, dst, allow_reflection=True, allow_scale=True)
            anchor_centroid = dst.mean(axis=0)
            print(
                f"[filtered-static] block {block}/{total_blocks} "
                f"shard={args.shard_id}/{args.num_shards}: {version} {eval_set}",
                flush=True,
            )
            for path in static_files:
                sid = session_id_from_path(path)
                cap = capture_name_from_path(path)
                frames = filter_frames(raw_frames[str(path)], allowed, min_anchors=4)
                truth = tag_truth.get(sid)
                if truth is None:
                    continue
                meta = metadata.get(sid, {})
                truth_info = tag_truth_meta.get(sid, {})
                solver_points: dict[str, tuple[str, list[FilterPoint]]] = {}
                for method in tag_methods:
                    results = solve_frames(layout, method, frames)
                    for filt in external_filters:
                        solver_points[f"{method}+{filt}"] = ("external_position_filter", position_kf(results, filt))
                for variant in t5_variants:
                    if variant == "T5d":
                        pts = run_range_ukf(layout, frames)
                    else:
                        pts = run_range_ekf(layout, frames, variant)
                    solver_points[variant] = ("native_range_filter", pts)
                for solver, (family, points) in solver_points.items():
                    summary = summarize_points(points, args.point_estimator)
                    if summary["status"] != "ok":
                        continue
                    point = np.array([[summary["x_mm"], summary["y_mm"], summary["z_mm"]]], dtype=float)
                    aligned = apply_transform(point, r, t, scale)[0]
                    diff = aligned - truth
                    distance_to_array = float(np.linalg.norm(truth - anchor_centroid))
                    session_rows.append(
                        {
                            "version": version,
                            "solver": solver,
                            "solver_family": family,
                            "eval_set": eval_set,
                            "ID": sid,
                            "capture": cap,
                            "location": meta.get("location", ""),
                            "height": meta.get("height", ""),
                            "facing": meta.get("facing", ""),
                            "tag_truth_source": truth_info.get("tag_truth_source", ""),
                            "tag_truth_corrected": bool(truth_info.get("corrected", False)),
                            "point_estimator": args.point_estimator,
                            "frames_input": int(len(frames)),
                            "solve_fraction": float(summary["frames_solved"] / len(frames)) if frames else 0.0,
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
                            "err_horizontal_mm": float(math.hypot(diff[0], diff[2])),
                            "err_vertical_mm": float(abs(diff[1])),
                            "err_3d_mm": float(np.linalg.norm(diff)),
                            "anchor_fit_det": det,
                            "anchor_fit_scale": scale,
                            "anchor_similarity_scale_diagnostic": scale_diag,
                            "distance_to_array_centroid_mm": distance_to_array,
                            "scale_bias_expected_mm": float(abs(1.0 - scale_diag) * distance_to_array),
                            "source_tr_all": str(path),
                            "layout_json": str(layout_path),
                        }
                    )

    if args.num_shards > 1:
        suffix = f"_shard{args.shard_id:02d}_of_{args.num_shards:02d}"
        summary_rows = summarize_abs(session_rows)
        write_csv(tables_dir / f"filtered_static_abs_errors_per_session{suffix}.csv", session_rows)
        write_csv(tables_dir / f"filtered_static_accuracy_summary{suffix}.csv", summary_rows)
        meta = {
            "script": "filtered_deployment/scripts/run_filtered_static_matrix.py",
            "mode": "shard",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": time.perf_counter() - t_start,
            "official_root": str(official_root),
            "layout_versions": layout_versions,
            "tag_methods": tag_methods,
            "external_filters": external_filters,
            "t5_variants": t5_variants,
            "eval_sets": eval_sets,
            "num_shards": args.num_shards,
            "shard_id": args.shard_id,
            "max_frames": args.max_frames,
            "truth_usage": "OptiTrack used only for final evaluation; not used in filters.",
            "axis_convention": "OptiTrack Y is vertical",
        }
        (out_dir / f"run_meta_filtered_static{suffix}.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"[filtered-static] shard {args.shard_id}/{args.num_shards} wrote "
            f"{len(session_rows)} per-session rows and {len(summary_rows)} summary rows",
            flush=True,
        )
        print(f"[filtered-static] elapsed {meta['elapsed_s']:.1f}s", flush=True)
        return 0

    summary_rows = summarize_abs(session_rows)
    metric_rows, axis_rows, outlier_rows, by_height, edge_rows, facing_rows = build_metrics_tables(session_rows)
    radial_detail, radial_summary = radial_diagnostics(session_rows)

    write_csv(tables_dir / "filtered_static_abs_errors_per_session.csv", session_rows)
    write_csv(tables_dir / "filtered_static_accuracy_summary.csv", summary_rows)
    write_csv(tables_dir / "filtered_static_metrics_full.csv", metric_rows)
    write_csv(tables_dir / "filtered_static_per_axis_bias.csv", axis_rows)
    write_csv(tables_dir / "filtered_static_outlier_rates.csv", outlier_rows)
    write_csv(tables_dir / "filtered_static_error_by_height.csv", by_height)
    write_csv(tables_dir / "filtered_static_error_edge_vs_center.csv", edge_rows)
    write_csv(tables_dir / "filtered_static_error_by_facing.csv", facing_rows)
    write_csv(tables_dir / "filtered_static_radial_decomposition.csv", radial_detail)
    write_csv(tables_dir / "filtered_static_radial_summary.csv", radial_summary)
    plot_summary(summary_rows, session_rows, figs_dir)
    write_report(reports_dir / "filtered_static_results.md", summary_rows)

    meta = {
        "script": "filtered_deployment/scripts/run_filtered_static_matrix.py",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": time.perf_counter() - t_start,
        "official_root": str(official_root),
        "layout_versions": layout_versions,
        "tag_methods": tag_methods,
        "external_filters": external_filters,
        "t5_variants": t5_variants,
        "eval_sets": eval_sets,
        "max_frames": args.max_frames,
        "truth_usage": "OptiTrack used only for final evaluation; not used in filters.",
        "axis_convention": "OptiTrack Y is vertical",
        "inputs": {
            "sigma_path": str(sigma_path),
            "n_static_files": len(static_files),
            "layout_hashes": {v: sha256_file(layout_base / v / "layout.json") for v in layout_versions},
        },
        "outputs": {
            "per_session": str(tables_dir / "filtered_static_abs_errors_per_session.csv"),
            "summary": str(tables_dir / "filtered_static_accuracy_summary.csv"),
            "report": str(reports_dir / "filtered_static_results.md"),
        },
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[filtered-static] wrote {len(session_rows)} per-session rows and {len(summary_rows)} summary rows", flush=True)
    print(f"[filtered-static] elapsed {meta['elapsed_s']:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
