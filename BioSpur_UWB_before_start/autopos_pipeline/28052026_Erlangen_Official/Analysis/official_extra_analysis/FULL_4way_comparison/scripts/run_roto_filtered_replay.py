#!/usr/bin/env python3
"""ROTO post-solve filtered trajectory replay for the FULL 4-way comparison.

This script does not rerun the range solver. It applies deployment-style
position filters to already solved, OptiTrack-aligned ROTO sample trajectories,
then recomputes absolute trajectory and circle metrics. The intent is to answer
whether dynamic filtering changes the ROTO conclusion, while keeping the layout,
delay, tag solver, and capture-level time alignment fixed.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
COMP_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
FULL_ROOT = EXTRA_ROOT / "FULL"
ALIGN_ROOT = EXTRA_ROOT / "FULL_AutoPos_align_to_Vicon"
SCALE_ROOT = EXTRA_ROOT / "FULL_AutoPos_scale_to_vicon"
ONE_BASELINE_ROOT = EXTRA_ROOT / "FULL_AutoPos_one_baseline_scale_correction"
OUT_ROOT = COMP_ROOT / "roto_filtered"
TABLE_DIR = OUT_ROOT / "tables"
REPORT_DIR = OUT_ROOT / "reports"

UWB_TAGS = ["BS2DCE", "BSDC91"]
ROTO_RADIUS_MM = {"BS2DCE": 440.0, "BSDC91": 560.0}


@dataclass(frozen=True)
class RotoCase:
    case: str
    label: str
    source_root: Path
    sample_path: Path
    filters: dict[str, str]
    note: str


@dataclass(frozen=True)
class FilterSpec:
    filter_id: str
    family: str
    deployability: str
    description: str


FILTER_SPECS = [
    FilterSpec("F0", "passthrough", "baseline", "unfiltered solved ROTO samples"),
    FilterSpec("F1", "cv_kalman", "online", "constant-velocity Kalman filter"),
    FilterSpec("F2", "cv_kalman_robust", "online", "constant-velocity Kalman filter with innovation down-weighting"),
    FilterSpec("F3", "adaptive_cv_kalman_robust", "online", "adaptive-acceleration constant-velocity Kalman filter"),
    FilterSpec("F4", "fixed_lag_smoother", "fixed_lag", "bounded-latency fixed-lag smoother over the F2 trajectory"),
    FilterSpec("F5", "rts_offline_smoother", "offline_upper_bound", "full-sequence RTS smoother; uses future samples"),
]


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
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def pct(values, q: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def rms(values) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(math.sqrt(float(np.mean(arr * arr))))


def finite_stats(values, prefix: str) -> dict[str, float | int]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {
            f"{prefix}_n": 0,
            f"{prefix}_mean_mm": float("nan"),
            f"{prefix}_p50_mm": float("nan"),
            f"{prefix}_p90_mm": float("nan"),
            f"{prefix}_p95_mm": float("nan"),
            f"{prefix}_rmse_mm": float("nan"),
        }
    return {
        f"{prefix}_n": int(arr.size),
        f"{prefix}_mean_mm": float(np.mean(arr)),
        f"{prefix}_p50_mm": float(np.percentile(arr, 50)),
        f"{prefix}_p90_mm": float(np.percentile(arr, 90)),
        f"{prefix}_p95_mm": float(np.percentile(arr, 95)),
        f"{prefix}_rmse_mm": rms(arr),
    }


def fmt(x: float, ndigits: int = 1) -> str:
    if x is None or not math.isfinite(float(x)):
        return "nan"
    return f"{float(x):.{ndigits}f}"


def select_rows(df: pd.DataFrame, filters: dict[str, str]) -> pd.DataFrame:
    out = df.copy()
    for col, value in filters.items():
        if col not in out.columns:
            raise KeyError(f"missing filter column {col!r}")
        out = out[out[col].astype(str) == str(value)]
    return out.copy()


def stable_dt(prev_t: float, curr_t: float) -> float:
    dt = float(curr_t) - float(prev_t)
    if not math.isfinite(dt) or dt <= 1e-4:
        return 0.08
    return max(1e-3, min(0.25, dt))


def cv_predict(x: np.ndarray, p: np.ndarray, dt: float, accel_sigma: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    f = np.eye(6, dtype=float)
    for axis in range(3):
        f[axis, axis + 3] = dt
    s2 = float(accel_sigma) ** 2
    q = np.zeros((6, 6), dtype=float)
    q_pos = 0.25 * dt**4 * s2
    q_cross = 0.5 * dt**3 * s2
    q_vel = dt**2 * s2
    for axis in range(3):
        q[axis, axis] = q_pos
        q[axis, axis + 3] = q_cross
        q[axis + 3, axis] = q_cross
        q[axis + 3, axis + 3] = q_vel
    return f @ x, f @ p @ f.T + q, f


def position_filter(times_s: np.ndarray, xyz_mm: np.ndarray, variant: str) -> tuple[np.ndarray, int, str]:
    times = np.asarray(times_s, dtype=float)
    meas = np.asarray(xyz_mm, dtype=float)
    finite = np.isfinite(times) & np.isfinite(meas).all(axis=1)
    if variant == "F0" or meas.shape[0] == 0:
        return meas.copy(), 0, "F0"
    if np.sum(finite) < 3:
        return meas.copy(), 0, f"{variant}_insufficient"

    x = np.zeros(6, dtype=float)
    first_idx = int(np.where(finite)[0][0])
    x[:3] = meas[first_idx]
    p = np.diag([140.0**2, 140.0**2, 180.0**2, 900.0**2, 900.0**2, 1200.0**2])
    h = np.zeros((3, 6), dtype=float)
    h[:, :3] = np.eye(3)
    r_base = np.diag([75.0**2, 75.0**2, 95.0**2])

    xs_f: list[np.ndarray] = []
    ps_f: list[np.ndarray] = []
    xs_pred: list[np.ndarray] = []
    ps_pred: list[np.ndarray] = []
    fs: list[np.ndarray] = []
    out = np.full_like(meas, np.nan, dtype=float)
    prev_t = float(times[first_idx])
    prev_meas = meas[first_idx].copy()
    rejections = 0

    for idx, z in enumerate(meas):
        if not finite[idx]:
            xs_f.append(x.copy())
            ps_f.append(p.copy())
            xs_pred.append(x.copy())
            ps_pred.append(p.copy())
            fs.append(np.eye(6, dtype=float))
            continue
        dt = stable_dt(prev_t, float(times[idx])) if idx != first_idx else 0.08
        accel = 1800.0
        if variant == "F3":
            speed = float(np.linalg.norm(z - prev_meas) / max(dt, 1e-3))
            accel = 3200.0 if speed > 900.0 else 900.0
        x_pred, p_pred, f = cv_predict(x, p, dt, accel)
        innov = z - h @ x_pred
        nis_like = float(innov.T @ innov) / (75.0**2)
        r_scale = 1.0
        if variant in {"F2", "F3", "F4", "F5"} and nis_like > 25.0:
            r_scale = min(30.0, nis_like / 9.0)
            rejections += 1
        rmat = r_base * r_scale
        s = h @ p_pred @ h.T + rmat
        k = p_pred @ h.T @ np.linalg.pinv(s)
        x = x_pred + k @ innov
        i_kh = np.eye(6) - k @ h
        p = i_kh @ p_pred @ i_kh.T + k @ rmat @ k.T

        out[idx] = x[:3]
        xs_pred.append(x_pred.copy())
        ps_pred.append(p_pred.copy())
        fs.append(f.copy())
        xs_f.append(x.copy())
        ps_f.append(p.copy())
        prev_t = float(times[idx])
        prev_meas = z

    if variant == "F4" and np.sum(np.isfinite(out).all(axis=1)) >= 3:
        smooth = out.copy()
        lag = 5
        good_idx = np.where(np.isfinite(out).all(axis=1))[0]
        for idx in good_idx:
            local = good_idx[(good_idx >= idx - lag) & (good_idx <= idx + lag)]
            weights = 1.0 / (1.0 + np.abs(local - idx).astype(float))
            smooth[idx] = np.sum(out[local] * weights[:, None], axis=0) / np.sum(weights)
        return smooth, rejections, "F4_FIXED_LAG_5"

    if variant == "F5" and len(xs_f) >= 3:
        smooth_states = [arr.copy() for arr in xs_f]
        smooth_p = [arr.copy() for arr in ps_f]
        for i in range(len(xs_f) - 2, -1, -1):
            c = ps_f[i] @ fs[i + 1].T @ np.linalg.pinv(ps_pred[i + 1])
            smooth_states[i] = xs_f[i] + c @ (smooth_states[i + 1] - xs_pred[i + 1])
            smooth_p[i] = ps_f[i] + c @ (smooth_p[i + 1] - ps_pred[i + 1]) @ c.T
        smooth = out.copy()
        for idx in range(len(smooth)):
            if finite[idx]:
                smooth[idx] = smooth_states[idx][:3]
        return smooth, rejections, "F5_RTS_OFFLINE"

    return out, rejections, variant


def fit_circle(points: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if pts.shape[0] < 30:
        return {"status": "insufficient", "n": int(pts.shape[0])}
    center0 = pts.mean(axis=0)
    _u, _s, vh = np.linalg.svd(pts - center0, full_matrices=False)
    e1, e2, normal = vh[0], vh[1], vh[-1]
    uv = np.column_stack([(pts - center0) @ e1, (pts - center0) @ e2])
    x, y = uv[:, 0], uv[:, 1]
    a = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    b = x * x + y * y
    cx, cy, c = np.linalg.lstsq(a, b, rcond=None)[0]
    radius = math.sqrt(max(0.0, float(c + cx * cx + cy * cy)))
    radial = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - radius
    zplane = (pts - center0) @ normal
    thickness = np.sqrt(radial * radial + zplane * zplane)
    center3 = center0 + cx * e1 + cy * e2
    theta = np.unwrap(np.arctan2(y - cy, x - cx))
    if theta.size and theta[-1] < theta[0]:
        theta = -theta
    return {
        "status": "ok",
        "n": int(pts.shape[0]),
        "center": center3,
        "radius_mm": float(radius),
        "circle_thickness_rms_mm": rms(thickness),
        "circle_thickness_p95_mm": pct(thickness, 95),
        "theta": theta,
    }


def per_turn_repeatability(points: np.ndarray, theta: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    th = np.asarray(theta, dtype=float)
    if pts.shape[0] < 80 or th.size != pts.shape[0]:
        return {"turn_count": 0, "turn_center_repeatability_rms_mm": float("nan")}
    th = th - th[0]
    bins = np.floor(th / (2.0 * math.pi)).astype(int)
    centers = []
    for b in range(int(np.min(bins)), int(np.max(bins)) + 1):
        idx = np.where(bins == b)[0]
        if idx.size < 30:
            continue
        fit = fit_circle(pts[idx])
        if fit.get("status") == "ok":
            centers.append(np.asarray(fit["center"], dtype=float))
    if len(centers) < 2:
        return {"turn_count": len(centers), "turn_center_repeatability_rms_mm": float("nan")}
    c = np.vstack(centers)
    dist = np.linalg.norm(c - np.mean(c, axis=0), axis=1)
    return {
        "turn_count": int(len(centers)),
        "turn_center_repeatability_rms_mm": rms(dist),
        "turn_center_repeatability_p95_mm": pct(dist, 95),
    }


def error_components(filtered_xyz: np.ndarray, opti_xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    diff = np.asarray(filtered_xyz, dtype=float) - np.asarray(opti_xyz, dtype=float)
    finite = np.isfinite(diff).all(axis=1)
    err3 = np.full(diff.shape[0], np.nan, dtype=float)
    errxz = np.full(diff.shape[0], np.nan, dtype=float)
    erry = np.full(diff.shape[0], np.nan, dtype=float)
    err3[finite] = np.linalg.norm(diff[finite], axis=1)
    errxz[finite] = np.sqrt(diff[finite, 0] * diff[finite, 0] + diff[finite, 2] * diff[finite, 2])
    erry[finite] = np.abs(diff[finite, 1])
    return err3, errxz, erry, diff


def make_cases() -> list[RotoCase]:
    return [
        RotoCase(
            "full_original_v4io_T4",
            "FULL original v4-io/T4",
            FULL_ROOT,
            FULL_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
            {"layout": "v4-io", "tag_method": "T4"},
            "original self-cal layout and solver residual delay corrections",
        ),
        RotoCase(
            "vicon_truth_delaycal_v4io_T4",
            "Vicon anchors + delaycal / T4",
            ALIGN_ROOT,
            ALIGN_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
            {
                "layout_solver": "v4-io",
                "layout_variant": "vicon_truth",
                "delay_mode": "vicon_inter_anchor_delaycal",
                "tag_method": "T4",
            },
            "OptiTrack/Vicon anchor truth with inter-anchor residual delay calibration",
        ),
        RotoCase(
            "scale_to_vicon_delaycal_v4io_T4",
            "Full similarity scale + delaycal / v4-io/T4",
            SCALE_ROOT,
            SCALE_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
            {
                "layout_solver": "v4-io",
                "layout_variant": "solver_similarity_scale_to_vicon",
                "delay_mode": "scaled_layout_inter_anchor_delaycal",
                "tag_method": "T4",
            },
            "full similarity scale-to-Vicon with re-estimated residual delay corrections",
        ),
        RotoCase(
            "one_baseline_EH_delaycal_v4io_T4",
            "One-baseline E-H + delaycal / v4-io/T4",
            ONE_BASELINE_ROOT,
            ONE_BASELINE_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
            {
                "layout_solver": "v4-io",
                "layout_variant": "one_baseline_scale",
                "delay_mode": "one_baseline_layout_inter_anchor_delaycal",
                "baseline_pair": "E-H",
                "tag_method": "T4",
            },
            "pre-registered one-baseline E-H control",
        ),
    ]


def track_metrics(case: RotoCase, filt: FilterSpec, group: pd.DataFrame) -> tuple[dict, np.ndarray, np.ndarray, np.ndarray]:
    g = group.sort_values("uwb_time_s").copy()
    times = g["uwb_time_s"].to_numpy(float)
    raw = g[["uwb_x_mm", "uwb_y_vertical_mm", "uwb_z_mm"]].to_numpy(float)
    opti = g[["opti_x_mm", "opti_y_vertical_mm", "opti_z_mm"]].to_numpy(float)
    filtered, rejections, filter_kind = position_filter(times, raw, filt.filter_id)
    err3, errxz, erry, _diff = error_components(filtered, opti)

    f_circle = fit_circle(filtered)
    o_circle = fit_circle(opti)
    circle_row: dict[str, float | int | str] = {}
    if f_circle.get("status") == "ok" and o_circle.get("status") == "ok":
        fc = np.asarray(f_circle["center"], dtype=float)
        oc = np.asarray(o_circle["center"], dtype=float)
        c_diff = fc - oc
        radius_error = float(f_circle["radius_mm"] - o_circle["radius_mm"])
        circle_row.update(
            {
                "turn_center_abs_error_3d_mm": float(np.linalg.norm(c_diff)),
                "turn_center_abs_error_horizontal_xz_mm": float(math.sqrt(c_diff[0] * c_diff[0] + c_diff[2] * c_diff[2])),
                "turn_center_abs_error_vertical_y_mm": float(abs(c_diff[1])),
                "filtered_radius_mm": float(f_circle["radius_mm"]),
                "opti_radius_mm": float(o_circle["radius_mm"]),
                "radius_error_mm": radius_error,
                "radius_error_abs_mm": abs(radius_error),
                "circle_thickness_rms_mm": float(f_circle["circle_thickness_rms_mm"]),
                "circle_thickness_p95_mm": float(f_circle["circle_thickness_p95_mm"]),
            }
        )
        turn = per_turn_repeatability(filtered[np.isfinite(filtered).all(axis=1)], np.asarray(f_circle["theta"], dtype=float))
        circle_row.update(turn)
    else:
        circle_row.update(
            {
                "turn_center_abs_error_3d_mm": float("nan"),
                "turn_center_abs_error_horizontal_xz_mm": float("nan"),
                "turn_center_abs_error_vertical_y_mm": float("nan"),
                "filtered_radius_mm": float("nan"),
                "opti_radius_mm": float("nan"),
                "radius_error_mm": float("nan"),
                "radius_error_abs_mm": float("nan"),
                "circle_thickness_rms_mm": float("nan"),
                "circle_thickness_p95_mm": float("nan"),
                "turn_count": 0,
                "turn_center_repeatability_rms_mm": float("nan"),
                "turn_center_repeatability_p95_mm": float("nan"),
            }
        )

    row: dict[str, float | int | str] = {
        "case": case.case,
        "case_label": case.label,
        "source_root": str(case.source_root.relative_to(EXTRA_ROOT)),
        "filter_id": filt.filter_id,
        "filter_family": filt.family,
        "filter_deployability": filt.deployability,
        "filter_kind": filter_kind,
        "capture_id": str(g["capture_id"].iloc[0]),
        "tag": str(g["tag"].iloc[0]),
        "n_samples": int(np.sum(np.isfinite(err3))),
        "filter_rejections": int(rejections),
        "err3d_p50_mm": pct(err3, 50),
        "err3d_p95_mm": pct(err3, 95),
        "err3d_rmse_mm": rms(err3),
        "err_horizontal_xz_p50_mm": pct(errxz, 50),
        "err_horizontal_xz_p95_mm": pct(errxz, 95),
        "err_vertical_y_p50_mm": pct(erry, 50),
        "err_vertical_y_p95_mm": pct(erry, 95),
        "note": case.note,
    }
    row.update(circle_row)
    return row, err3, errxz, erry


def summarize_case_filter(case: RotoCase, filt: FilterSpec, track_rows: list[dict], sample_err3, sample_errxz, sample_erry) -> dict:
    rows = [r for r in track_rows if r["case"] == case.case and r["filter_id"] == filt.filter_id]
    pair_rows = []
    by_capture: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_capture.setdefault(str(row["capture_id"]), {})[str(row["tag"])] = row
    for capture_id, by_tag in by_capture.items():
        if "BS2DCE" not in by_tag or "BSDC91" not in by_tag:
            continue
        inner = by_tag["BS2DCE"]
        outer = by_tag["BSDC91"]
        if not math.isfinite(float(inner["filtered_radius_mm"])) or not math.isfinite(float(outer["filtered_radius_mm"])):
            continue
        delta_r = float(outer["filtered_radius_mm"] - inner["filtered_radius_mm"])
        ic = float(inner["turn_center_abs_error_3d_mm"])
        oc = float(outer["turn_center_abs_error_3d_mm"])
        pair_rows.append(
            {
                "capture_id": capture_id,
                "deltaR_error_mm": delta_r - (ROTO_RADIUS_MM["BSDC91"] - ROTO_RADIUS_MM["BS2DCE"]),
                "inner_center_error_mm": ic,
                "outer_center_error_mm": oc,
            }
        )

    summary: dict[str, float | int | str] = {
        "case": case.case,
        "case_label": case.label,
        "source_root": str(case.source_root.relative_to(EXTRA_ROOT)),
        "filter_id": filt.filter_id,
        "filter_family": filt.family,
        "filter_deployability": filt.deployability,
        "filter_description": filt.description,
        "track_count": int(len(rows)),
        "capture_pair_count": int(len(pair_rows)),
        "note": case.note,
    }
    summary.update(finite_stats(sample_err3, "sample_err3d"))
    summary.update(finite_stats(sample_errxz, "sample_err_horizontal_xz"))
    summary.update(finite_stats(sample_erry, "sample_err_vertical_y"))
    for metric, prefix in [
        ("err3d_p50_mm", "trackmedian_err3d_p50"),
        ("err3d_p95_mm", "trackmedian_err3d_p95"),
        ("err_horizontal_xz_p95_mm", "trackmedian_err_horizontal_xz_p95"),
        ("err_vertical_y_p95_mm", "trackmedian_err_vertical_y_p95"),
        ("turn_center_abs_error_3d_mm", "trackmedian_turn_center_abs_error_3d"),
        ("turn_center_abs_error_horizontal_xz_mm", "trackmedian_turn_center_abs_error_xz"),
        ("turn_center_abs_error_vertical_y_mm", "trackmedian_turn_center_abs_error_y"),
        ("radius_error_abs_mm", "trackmedian_radius_error_abs"),
        ("circle_thickness_rms_mm", "trackmedian_circle_thickness_rms"),
        ("turn_center_repeatability_rms_mm", "trackmedian_turn_center_repeatability_rms"),
    ]:
        vals = [float(r.get(metric, float("nan"))) for r in rows]
        summary[f"{prefix}_mm"] = pct(vals, 50)
    summary["turn_center_abs_error_3d_rms_mm"] = rms(
        [float(r.get("turn_center_abs_error_3d_mm", float("nan"))) for r in rows]
    )
    summary["legacy_deltaR_error_rms_mm"] = rms([r["deltaR_error_mm"] for r in pair_rows])
    summary["legacy_abs_deltaR_error_median_mm"] = pct(
        np.abs(np.asarray([r["deltaR_error_mm"] for r in pair_rows], dtype=float)), 50
    )
    summary["legacy_abs_deltaR_error_p95_mm"] = pct(
        np.abs(np.asarray([r["deltaR_error_mm"] for r in pair_rows], dtype=float)), 95
    )
    return summary


def add_baseline_deltas(summary_rows: list[dict]) -> None:
    by_case_filter = {(str(r["case"]), str(r["filter_id"])): r for r in summary_rows}
    for row in summary_rows:
        base = by_case_filter.get((str(row["case"]), "F0"))
        if base is None:
            continue
        for key in [
            "sample_err3d_p50_mm",
            "sample_err3d_p95_mm",
            "sample_err3d_rmse_mm",
            "trackmedian_err3d_p50_mm",
            "trackmedian_err3d_p95_mm",
            "turn_center_abs_error_3d_rms_mm",
            "legacy_deltaR_error_rms_mm",
        ]:
            row[f"improvement_vs_F0_{key}"] = float(base[key]) - float(row[key])
        p50_gain = float(row["improvement_vs_F0_trackmedian_err3d_p50_mm"])
        p95_gain = float(row["improvement_vs_F0_trackmedian_err3d_p95_mm"])
        if str(row["filter_id"]) == "F0":
            verdict = "BASELINE_UNFILTERED"
        elif str(row["filter_deployability"]) == "offline_upper_bound" and p50_gain >= 5.0:
            verdict = "OFFLINE_FILTER_HELPS_DIAGNOSTIC_ONLY"
        elif p50_gain >= 5.0 and p95_gain >= -5.0:
            verdict = "FILTER_HELPS"
        elif p50_gain <= -5.0 or p95_gain <= -10.0:
            verdict = "FILTER_HURTS"
        else:
            verdict = "FILTER_NEUTRAL"
        row["filter_verdict"] = verdict


def run_matrix() -> tuple[list[dict], list[dict]]:
    track_rows: list[dict] = []
    summary_inputs: dict[tuple[str, str], dict[str, list[np.ndarray]]] = {}
    for case in make_cases():
        df_all = pd.read_csv(case.sample_path)
        df = select_rows(df_all, case.filters)
        if df.empty:
            raise RuntimeError(f"empty sample table for {case.case}: {case.sample_path}")
        for filt in FILTER_SPECS:
            key = (case.case, filt.filter_id)
            summary_inputs[key] = {"err3": [], "errxz": [], "erry": []}
            for (_capture_id, _tag), group in df.groupby(["capture_id", "tag"], sort=True):
                row, err3, errxz, erry = track_metrics(case, filt, group)
                track_rows.append(row)
                summary_inputs[key]["err3"].append(err3)
                summary_inputs[key]["errxz"].append(errxz)
                summary_inputs[key]["erry"].append(erry)

    summary_rows: list[dict] = []
    cases = make_cases()
    case_by_name = {c.case: c for c in cases}
    filter_by_name = {f.filter_id: f for f in FILTER_SPECS}
    for case_name, filter_id in sorted(summary_inputs):
        vals = summary_inputs[(case_name, filter_id)]
        err3 = np.concatenate(vals["err3"]) if vals["err3"] else np.empty(0)
        errxz = np.concatenate(vals["errxz"]) if vals["errxz"] else np.empty(0)
        erry = np.concatenate(vals["erry"]) if vals["erry"] else np.empty(0)
        summary_rows.append(
            summarize_case_filter(
                case_by_name[case_name],
                filter_by_name[filter_id],
                track_rows,
                err3,
                errxz,
                erry,
            )
        )
    add_baseline_deltas(summary_rows)
    return summary_rows, track_rows


def write_report(summary_rows: list[dict]) -> None:
    rows = sorted(summary_rows, key=lambda r: (str(r["case"]), float(r["trackmedian_err3d_p50_mm"])))
    best_online = [
        r for r in rows
        if str(r["filter_deployability"]) in {"online", "fixed_lag"}
    ]
    lines = []
    lines.append("# ROTO Filtered Replay")
    lines.append("")
    lines.append(f"Generated {datetime.now(UTC).isoformat()}.")
    lines.append("")
    lines.append(
        "These are post-solve trajectory filters applied to already solved ROTO v4-io/T4 samples. "
        "They keep layout, residual delay correction, tag solver, and capture-level time alignment fixed."
    )
    lines.append("")
    lines.append("## Filter Definitions")
    lines.append("")
    for spec in FILTER_SPECS:
        lines.append(f"- `{spec.filter_id}`: {spec.description}; deployability=`{spec.deployability}`.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    cols = [
        "case_label",
        "filter_id",
        "filter_deployability",
        "trackmedian_err3d_p50_mm",
        "trackmedian_err3d_p95_mm",
        "sample_err3d_rmse_mm",
        "turn_center_abs_error_3d_rms_mm",
        "legacy_deltaR_error_rms_mm",
        "improvement_vs_F0_trackmedian_err3d_p50_mm",
        "filter_verdict",
    ]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in rows:
        vals = []
        for col in cols:
            val = row.get(col, "")
            if isinstance(val, float):
                vals.append(fmt(val))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    if best_online:
        best = min(best_online, key=lambda r: float(r["trackmedian_err3d_p50_mm"]))
        lines.append(
            f"- Best deployable/fixed-lag filtered row by track-median 3D P50: `{best['case_label']}` "
            f"`{best['filter_id']}` at {fmt(best['trackmedian_err3d_p50_mm'])} / "
            f"{fmt(best['trackmedian_err3d_p95_mm'])} mm."
        )
    offline = [r for r in rows if str(r["filter_deployability"]) == "offline_upper_bound"]
    if offline:
        best_off = min(offline, key=lambda r: float(r["trackmedian_err3d_p50_mm"]))
        lines.append(
            f"- Best offline upper bound: `{best_off['case_label']}` `{best_off['filter_id']}` at "
            f"{fmt(best_off['trackmedian_err3d_p50_mm'])} / {fmt(best_off['trackmedian_err3d_p95_mm'])} mm."
        )
    lines.append(
        "- F5 uses future samples and is diagnostic only. F4 adds bounded output latency. F1-F3 are online post-solve filters."
    )
    lines.append("")
    lines.append("## Output Tables")
    lines.append("")
    lines.append("- `../tables/roto_filtered_summary.csv`")
    lines.append("- `../tables/roto_filtered_per_track.csv`")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "ROTO_FILTERED_REPLAY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ROTO post-solve filtered replay.")
    parser.add_argument("--summary-only", action="store_true", help="Only print existing summary table.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if args.summary_only:
        df = pd.read_csv(TABLE_DIR / "roto_filtered_summary.csv")
        print(df.to_string(index=False))
        return
    summary_rows, track_rows = run_matrix()
    write_csv(TABLE_DIR / "roto_filtered_summary.csv", summary_rows)
    write_csv(TABLE_DIR / "roto_filtered_per_track.csv", track_rows)
    write_report(summary_rows)
    print(f"Wrote {TABLE_DIR / 'roto_filtered_summary.csv'}")
    for row in sorted(summary_rows, key=lambda r: (str(r["case"]), float(r["trackmedian_err3d_p50_mm"]))):
        print(
            f"{row['case']} {row['filter_id']}: "
            f"track P50/P95={fmt(row['trackmedian_err3d_p50_mm'])}/"
            f"{fmt(row['trackmedian_err3d_p95_mm'])} mm, "
            f"vs F0 P50 gain={fmt(row['improvement_vs_F0_trackmedian_err3d_p50_mm'])} mm, "
            f"verdict={row['filter_verdict']}"
        )


if __name__ == "__main__":
    main()
