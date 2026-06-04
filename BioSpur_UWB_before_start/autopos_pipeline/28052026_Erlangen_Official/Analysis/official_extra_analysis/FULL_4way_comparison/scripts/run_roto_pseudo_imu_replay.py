#!/usr/bin/env python3
"""ROTO pseudo-IMU replay using OptiTrack-derived wand motion.

This is an oracle/diagnostic fusion experiment, not a real IMU deployment
result.  It fits a wand rigid body from non-antenna OptiTrack markers, estimates
the body-to-UWB-antenna lever arm from the OptiTrack antenna marker, and uses
the resulting antenna-point relative displacement as a motion prior for the
already solved UWB ROTO positions.
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

import run_roto_filtered_replay as filtered


THIS = Path(__file__).resolve()
COMP_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
OFFICIAL_ROOT = EXTRA_ROOT.parent.parent
OPTI_FULL_ROOT = OFFICIAL_ROOT / "opti_captures" / "full"
OUT_ROOT = COMP_ROOT / "roto_pseudo_imu"
TABLE_DIR = OUT_ROOT / "tables"
REPORT_DIR = OUT_ROOT / "reports"

UWB_TAGS = ["BS2DCE", "BSDC91"]
BODY_MARKERS = {
    "BS2DCE": ["WandBshort", "WandB4", "WandBtop", "WandBlong", "WandB5", "WandBcenter"],
    "BSDC91": ["WandCshort", "WandClong", "WandCtop", "WandC4", "WandC5", "WandCcenter"],
}
ANTENNA_MARKERS = {"BS2DCE": "WandBantenna", "BSDC91": "WandCantenna"}


def configure_output(out_root: str | Path | None = None) -> None:
    global OUT_ROOT, TABLE_DIR, REPORT_DIR
    if out_root is not None:
        OUT_ROOT = Path(out_root).resolve()
    TABLE_DIR = OUT_ROOT / "tables"
    REPORT_DIR = OUT_ROOT / "reports"


@dataclass(frozen=True)
class PseudoSpec:
    fusion_id: str
    family: str
    deployability: str
    description: str
    prior_sigma_mm: float
    measurement_sigma_mm: float
    smoother: str


PSEUDO_SPECS = [
    PseudoSpec("PI0", "passthrough", "baseline", "unfiltered solved UWB antenna positions", math.inf, math.inf, "none"),
    PseudoSpec(
        "PI1",
        "pseudo_imu_relative_prior",
        "online_oracle",
        "causal filter with strong OptiTrack-derived antenna relative-motion prior",
        8.0,
        90.0,
        "none",
    ),
    PseudoSpec(
        "PI2",
        "pseudo_imu_relative_prior",
        "online_oracle",
        "causal filter with balanced OptiTrack-derived antenna relative-motion prior",
        30.0,
        90.0,
        "none",
    ),
    PseudoSpec(
        "PI3",
        "pseudo_imu_relative_prior_fixed_lag",
        "fixed_lag_oracle",
        "bounded-lag smoother over PI1 causal pseudo-IMU trajectory",
        8.0,
        90.0,
        "fixed_lag",
    ),
    PseudoSpec(
        "PI4",
        "pseudo_imu_relative_prior_rts",
        "offline_upper_bound",
        "full-sequence RTS smoother with strong pseudo-IMU prior; uses future samples",
        8.0,
        90.0,
        "rts",
    ),
    PseudoSpec(
        "PI5",
        "pseudo_imu_relative_prior_rts",
        "offline_upper_bound",
        "full-sequence RTS smoother with balanced pseudo-IMU prior; uses future samples",
        30.0,
        90.0,
        "rts",
    ),
]


def write_csv(path: Path, rows: list[dict]) -> None:
    filtered.write_csv(path, rows)


def fmt(x: float, ndigits: int = 1) -> str:
    return filtered.fmt(x, ndigits)


def parse_trc_selected(path: Path, markers: list[str]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    marker_row = rows[3]
    marker_to_col: dict[str, int] = {}
    for offset, name in enumerate(marker_row[2:]):
        clean = name.strip()
        if clean:
            marker_to_col[clean] = 2 + offset
    data_rows = rows[5:]
    time_s = np.full(len(data_rows), np.nan, dtype=float)
    out = {m: np.full((len(data_rows), 3), np.nan, dtype=float) for m in markers}
    for i, row in enumerate(data_rows):
        if len(row) < 2:
            continue
        try:
            time_s[i] = float(row[1])
        except ValueError:
            continue
        for marker in markers:
            if marker not in marker_to_col:
                continue
            start = marker_to_col[marker]
            if start + 2 >= len(row):
                continue
            vals = []
            ok = True
            for col in range(start, start + 3):
                cell = row[col].strip()
                if not cell:
                    ok = False
                    break
                try:
                    vals.append(float(cell))
                except ValueError:
                    ok = False
                    break
            if ok:
                out[marker][i] = vals
    return time_s, out


def fit_rigid_row(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3:
        raise ValueError(f"bad rigid fit shapes {src.shape} {dst.shape}")
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, _s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    t = dst_c - src_c @ r
    residual = np.linalg.norm(src @ r + t - dst, axis=1)
    return r, t, filtered.rms(residual)


def interpolate_xyz(time_s: np.ndarray, xyz: np.ndarray, query_time_s: np.ndarray) -> np.ndarray:
    t = np.asarray(time_s, dtype=float)
    pts = np.asarray(xyz, dtype=float)
    q = np.asarray(query_time_s, dtype=float)
    good = np.isfinite(t) & np.isfinite(pts).all(axis=1)
    if int(np.sum(good)) < 2:
        return np.full((q.size, 3), np.nan, dtype=float)
    order = np.argsort(t[good])
    tg = t[good][order]
    pg = pts[good][order]
    out = np.empty((q.size, 3), dtype=float)
    for axis in range(3):
        out[:, axis] = np.interp(q, tg, pg[:, axis], left=np.nan, right=np.nan)
    return out


def bodyfit_for_capture_tag(capture_id: str, tag: str) -> tuple[dict, np.ndarray, np.ndarray]:
    body_markers = BODY_MARKERS[tag]
    antenna_marker = ANTENNA_MARKERS[tag]
    trc = OPTI_FULL_ROOT / f"{capture_id}.trc"
    time_s, marker_xyz = parse_trc_selected(trc, body_markers + [antenna_marker])

    usable_body = []
    for marker in body_markers:
        vals = marker_xyz[marker]
        good = np.isfinite(vals).all(axis=1)
        if int(np.sum(good)) >= 100:
            usable_body.append(marker)
    if len(usable_body) < 3:
        raise RuntimeError(f"{capture_id}/{tag} has only {len(usable_body)} usable body markers")
    all_good = np.isfinite(time_s)
    for marker in usable_body:
        all_good &= np.isfinite(marker_xyz[marker]).all(axis=1)
    good_idx = np.where(all_good)[0]
    if good_idx.size < 10:
        raise RuntimeError(f"{capture_id}/{tag} has too few complete body-marker reference frames")
    ref_idx = int(good_idx[good_idx.size // 2])
    ref_abs = np.vstack([marker_xyz[marker][ref_idx] for marker in usable_body])
    ref_origin = np.mean(ref_abs, axis=0)
    ref = ref_abs - ref_origin
    ant = marker_xyz[antenna_marker]

    rotations = np.full((time_s.size, 3, 3), np.nan, dtype=float)
    translations = np.full((time_s.size, 3), np.nan, dtype=float)
    fit_rms = np.full(time_s.size, np.nan, dtype=float)
    ant_ref_samples = []
    for i in range(time_s.size):
        cur_rows = []
        ref_valid = []
        for j, marker in enumerate(usable_body):
            xyz = marker_xyz[marker][i]
            if np.isfinite(xyz).all():
                cur_rows.append(xyz)
                ref_valid.append(ref[j])
        if len(cur_rows) < 3:
            continue
        r, t, rrms = fit_rigid_row(np.vstack(ref_valid), np.vstack(cur_rows))
        rotations[i] = r
        translations[i] = t
        fit_rms[i] = rrms
        if np.isfinite(ant[i]).all():
            ant_ref_samples.append((ant[i] - t) @ r.T)
    if len(ant_ref_samples) < 100:
        raise RuntimeError(f"{capture_id}/{tag} has too few antenna lever-arm samples")
    lever = np.nanmedian(np.vstack(ant_ref_samples), axis=0)

    bodyfit = np.full_like(ant, np.nan, dtype=float)
    for i in range(time_s.size):
        if np.isfinite(rotations[i]).all() and np.isfinite(translations[i]).all():
            bodyfit[i] = lever @ rotations[i] + translations[i]
    residual = np.linalg.norm(bodyfit - ant, axis=1)
    good_res = np.isfinite(residual)
    row = {
        "capture_id": capture_id,
        "tag": tag,
        "antenna_marker": antenna_marker,
        "body_markers": ";".join(usable_body),
        "reference_frame_index": ref_idx,
        "reference_time_s": float(time_s[ref_idx]),
        "n_pose_frames": int(np.sum(np.isfinite(bodyfit).all(axis=1))),
        "n_antenna_fit_frames": int(np.sum(good_res)),
        "lever_x_mm": float(lever[0]),
        "lever_y_mm": float(lever[1]),
        "lever_z_mm": float(lever[2]),
        "lever_norm_mm": float(np.linalg.norm(lever)),
        "body_marker_fit_rms_median_mm": filtered.pct(fit_rms, 50),
        "body_marker_fit_rms_p95_mm": filtered.pct(fit_rms, 95),
        "bodyfit_antenna_residual_p50_mm": filtered.pct(residual, 50),
        "bodyfit_antenna_residual_p95_mm": filtered.pct(residual, 95),
        "bodyfit_antenna_residual_rmse_mm": filtered.rms(residual),
        "note": "body pose excludes the UWB antenna marker; lever arm is estimated from the antenna marker for this oracle pseudo-IMU diagnostic",
    }
    return row, time_s, bodyfit


def pseudo_imu_filter(times_s: np.ndarray, meas_xyz: np.ndarray, prior_xyz: np.ndarray, spec: PseudoSpec) -> tuple[np.ndarray, int, str]:
    meas = np.asarray(meas_xyz, dtype=float)
    prior = np.asarray(prior_xyz, dtype=float)
    finite = np.isfinite(meas).all(axis=1)
    prior_finite = np.isfinite(prior).all(axis=1)
    if spec.fusion_id == "PI0" or meas.shape[0] == 0:
        return meas.copy(), 0, "PI0"
    if int(np.sum(finite & prior_finite)) < 3:
        return meas.copy(), int(np.sum(~prior_finite)), f"{spec.fusion_id}_insufficient_prior"

    first = int(np.where(finite)[0][0])
    x = meas[first].astype(float).copy()
    p = np.eye(3, dtype=float) * (150.0**2)
    rmat = np.eye(3, dtype=float) * (float(spec.measurement_sigma_mm) ** 2)
    ident = np.eye(3, dtype=float)
    out = np.full_like(meas, np.nan, dtype=float)
    out[first] = x
    prior_missing = 0

    x_f = []
    p_f = []
    x_pred_hist = []
    p_pred_hist = []
    prev_prior = prior[first].copy() if prior_finite[first] else None
    q_base = float(spec.prior_sigma_mm) ** 2

    for idx in range(meas.shape[0]):
        if idx == first:
            x_pred = x.copy()
            p_pred = p.copy()
        else:
            if prior_finite[idx] and prev_prior is not None:
                delta = prior[idx] - prev_prior
                q_scale = 1.0
            else:
                delta = np.zeros(3, dtype=float)
                q_scale = 25.0
                prior_missing += 1
            x_pred = x + delta
            p_pred = p + ident * q_base * q_scale

        if finite[idx]:
            z = meas[idx]
            s = p_pred + rmat
            k = p_pred @ np.linalg.pinv(s)
            x = x_pred + k @ (z - x_pred)
            p = (ident - k) @ p_pred @ (ident - k).T + k @ rmat @ k.T
            out[idx] = x
        else:
            x = x_pred
            p = p_pred

        x_pred_hist.append(x_pred.copy())
        p_pred_hist.append(p_pred.copy())
        x_f.append(x.copy())
        p_f.append(p.copy())
        if prior_finite[idx]:
            prev_prior = prior[idx].copy()

    if spec.smoother == "fixed_lag":
        smooth = out.copy()
        lag = 5
        good_idx = np.where(np.isfinite(out).all(axis=1))[0]
        for idx in good_idx:
            local = good_idx[(good_idx >= idx - lag) & (good_idx <= idx + lag)]
            weights = 1.0 / (1.0 + np.abs(local - idx).astype(float))
            smooth[idx] = np.sum(out[local] * weights[:, None], axis=0) / np.sum(weights)
        return smooth, prior_missing, f"{spec.fusion_id}_FIXED_LAG_5"

    if spec.smoother == "rts" and len(x_f) >= 3:
        xs = [v.copy() for v in x_f]
        ps = [v.copy() for v in p_f]
        for i in range(len(xs) - 2, -1, -1):
            c = p_f[i] @ np.linalg.pinv(p_pred_hist[i + 1])
            xs[i] = x_f[i] + c @ (xs[i + 1] - x_pred_hist[i + 1])
            ps[i] = p_f[i] + c @ (ps[i + 1] - p_pred_hist[i + 1]) @ c.T
        smooth = out.copy()
        for idx in range(len(smooth)):
            if finite[idx]:
                smooth[idx] = xs[idx]
        return smooth, prior_missing, f"{spec.fusion_id}_RTS_OFFLINE"

    return out, prior_missing, spec.fusion_id


def track_metrics(case: filtered.RotoCase, spec: PseudoSpec, group: pd.DataFrame, body_cache: dict) -> tuple[dict, np.ndarray, np.ndarray, np.ndarray]:
    g = group.sort_values("uwb_time_s").copy()
    capture_id = str(g["capture_id"].iloc[0])
    tag = str(g["tag"].iloc[0])
    if (capture_id, tag) not in body_cache:
        body_cache[(capture_id, tag)] = bodyfit_for_capture_tag(capture_id, tag)
    extrinsic_row, body_time, bodyfit_xyz = body_cache[(capture_id, tag)]

    times = g["uwb_time_s"].to_numpy(float)
    opti_times = g["opti_time_s"].to_numpy(float)
    raw = g[["uwb_x_mm", "uwb_y_vertical_mm", "uwb_z_mm"]].to_numpy(float)
    opti = g[["opti_x_mm", "opti_y_vertical_mm", "opti_z_mm"]].to_numpy(float)
    prior = interpolate_xyz(body_time, bodyfit_xyz, opti_times)
    fused, prior_missing, fusion_kind = pseudo_imu_filter(times, raw, prior, spec)
    err3, errxz, erry, _diff = filtered.error_components(fused, opti)
    prior_residual = np.linalg.norm(prior - opti, axis=1)

    f_circle = filtered.fit_circle(fused)
    o_circle = filtered.fit_circle(opti)
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
                "fused_radius_mm": float(f_circle["radius_mm"]),
                "opti_radius_mm": float(o_circle["radius_mm"]),
                "radius_error_mm": radius_error,
                "radius_error_abs_mm": abs(radius_error),
                "circle_thickness_rms_mm": float(f_circle["circle_thickness_rms_mm"]),
                "circle_thickness_p95_mm": float(f_circle["circle_thickness_p95_mm"]),
            }
        )
        good_fused = fused[np.isfinite(fused).all(axis=1)]
        circle_row.update(filtered.per_turn_repeatability(good_fused, np.asarray(f_circle["theta"], dtype=float)))
    else:
        circle_row.update(
            {
                "turn_center_abs_error_3d_mm": float("nan"),
                "turn_center_abs_error_horizontal_xz_mm": float("nan"),
                "turn_center_abs_error_vertical_y_mm": float("nan"),
                "fused_radius_mm": float("nan"),
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
        "fusion_id": spec.fusion_id,
        "fusion_family": spec.family,
        "fusion_deployability": spec.deployability,
        "fusion_kind": fusion_kind,
        "capture_id": capture_id,
        "tag": tag,
        "n_samples": int(np.sum(np.isfinite(err3))),
        "prior_missing_samples": int(prior_missing),
        "pseudo_imu_prior_available_ratio": float(np.mean(np.isfinite(prior).all(axis=1))),
        "bodyfit_prior_vs_opti_p50_mm": filtered.pct(prior_residual, 50),
        "bodyfit_prior_vs_opti_p95_mm": filtered.pct(prior_residual, 95),
        "err3d_p50_mm": filtered.pct(err3, 50),
        "err3d_p95_mm": filtered.pct(err3, 95),
        "err3d_rmse_mm": filtered.rms(err3),
        "err_horizontal_xz_p50_mm": filtered.pct(errxz, 50),
        "err_horizontal_xz_p95_mm": filtered.pct(errxz, 95),
        "err_vertical_y_p50_mm": filtered.pct(erry, 50),
        "err_vertical_y_p95_mm": filtered.pct(erry, 95),
        "note": case.note,
        "extrinsic_note": extrinsic_row["note"],
    }
    row.update(circle_row)
    return row, err3, errxz, erry


def summarize_case_fusion(case: filtered.RotoCase, spec: PseudoSpec, track_rows: list[dict], sample_err3, sample_errxz, sample_erry) -> dict:
    rows = [r for r in track_rows if r["case"] == case.case and r["fusion_id"] == spec.fusion_id]
    pair_rows = []
    by_capture: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_capture.setdefault(str(row["capture_id"]), {})[str(row["tag"])] = row
    for capture_id, by_tag in by_capture.items():
        if "BS2DCE" not in by_tag or "BSDC91" not in by_tag:
            continue
        inner = by_tag["BS2DCE"]
        outer = by_tag["BSDC91"]
        if not math.isfinite(float(inner["fused_radius_mm"])) or not math.isfinite(float(outer["fused_radius_mm"])):
            continue
        delta_r = float(outer["fused_radius_mm"] - inner["fused_radius_mm"])
        pair_rows.append({"capture_id": capture_id, "deltaR_error_mm": delta_r - 120.0})

    summary: dict[str, float | int | str] = {
        "case": case.case,
        "case_label": case.label,
        "source_root": str(case.source_root.relative_to(EXTRA_ROOT)),
        "fusion_id": spec.fusion_id,
        "fusion_family": spec.family,
        "fusion_deployability": spec.deployability,
        "fusion_description": spec.description,
        "track_count": int(len(rows)),
        "capture_pair_count": int(len(pair_rows)),
        "prior_sigma_mm": spec.prior_sigma_mm,
        "measurement_sigma_mm": spec.measurement_sigma_mm,
        "note": case.note,
    }
    summary.update(filtered.finite_stats(sample_err3, "sample_err3d"))
    summary.update(filtered.finite_stats(sample_errxz, "sample_err_horizontal_xz"))
    summary.update(filtered.finite_stats(sample_erry, "sample_err_vertical_y"))
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
        ("bodyfit_prior_vs_opti_p50_mm", "trackmedian_bodyfit_prior_vs_opti_p50"),
        ("bodyfit_prior_vs_opti_p95_mm", "trackmedian_bodyfit_prior_vs_opti_p95"),
    ]:
        vals = [float(r.get(metric, float("nan"))) for r in rows]
        summary[f"{prefix}_mm"] = filtered.pct(vals, 50)
    summary["turn_center_abs_error_3d_rms_mm"] = filtered.rms(
        [float(r.get("turn_center_abs_error_3d_mm", float("nan"))) for r in rows]
    )
    summary["legacy_deltaR_error_rms_mm"] = filtered.rms([r["deltaR_error_mm"] for r in pair_rows])
    summary["legacy_abs_deltaR_error_median_mm"] = filtered.pct(
        np.abs(np.asarray([r["deltaR_error_mm"] for r in pair_rows], dtype=float)), 50
    )
    summary["legacy_abs_deltaR_error_p95_mm"] = filtered.pct(
        np.abs(np.asarray([r["deltaR_error_mm"] for r in pair_rows], dtype=float)), 95
    )
    return summary


def add_baseline_deltas(summary_rows: list[dict]) -> None:
    by_case_fusion = {(str(r["case"]), str(r["fusion_id"])): r for r in summary_rows}
    for row in summary_rows:
        base = by_case_fusion.get((str(row["case"]), "PI0"))
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
            row[f"improvement_vs_PI0_{key}"] = float(base[key]) - float(row[key])
        p50_gain = float(row["improvement_vs_PI0_trackmedian_err3d_p50_mm"])
        p95_gain = float(row["improvement_vs_PI0_trackmedian_err3d_p95_mm"])
        if str(row["fusion_id"]) == "PI0":
            verdict = "BASELINE_UNFILTERED"
        elif str(row["fusion_deployability"]) == "offline_upper_bound" and p50_gain >= 5.0:
            verdict = "PSEUDO_IMU_HELPS_DIAGNOSTIC_ONLY"
        elif p50_gain >= 5.0 and p95_gain >= -5.0:
            verdict = "PSEUDO_IMU_HELPS"
        elif p50_gain <= -5.0 or p95_gain <= -10.0:
            verdict = "PSEUDO_IMU_HURTS"
        else:
            verdict = "PSEUDO_IMU_NEUTRAL"
        row["fusion_verdict"] = verdict


def run_matrix() -> tuple[list[dict], list[dict], list[dict]]:
    body_cache: dict[tuple[str, str], tuple[dict, np.ndarray, np.ndarray]] = {}
    track_rows: list[dict] = []
    summary_inputs: dict[tuple[str, str], dict[str, list[np.ndarray]]] = {}
    for case in filtered.make_cases():
        df_all = pd.read_csv(case.sample_path)
        df = filtered.select_rows(df_all, case.filters)
        if df.empty:
            raise RuntimeError(f"empty sample table for {case.case}: {case.sample_path}")
        for spec in PSEUDO_SPECS:
            key = (case.case, spec.fusion_id)
            summary_inputs[key] = {"err3": [], "errxz": [], "erry": []}
            for (_capture_id, _tag), group in df.groupby(["capture_id", "tag"], sort=True):
                row, err3, errxz, erry = track_metrics(case, spec, group, body_cache)
                track_rows.append(row)
                summary_inputs[key]["err3"].append(err3)
                summary_inputs[key]["errxz"].append(errxz)
                summary_inputs[key]["erry"].append(erry)

    cases = filtered.make_cases()
    case_by_name = {c.case: c for c in cases}
    spec_by_name = {s.fusion_id: s for s in PSEUDO_SPECS}
    summary_rows: list[dict] = []
    for case_name, fusion_id in sorted(summary_inputs):
        vals = summary_inputs[(case_name, fusion_id)]
        summary_rows.append(
            summarize_case_fusion(
                case_by_name[case_name],
                spec_by_name[fusion_id],
                track_rows,
                np.concatenate(vals["err3"]) if vals["err3"] else np.empty(0),
                np.concatenate(vals["errxz"]) if vals["errxz"] else np.empty(0),
                np.concatenate(vals["erry"]) if vals["erry"] else np.empty(0),
            )
        )
    add_baseline_deltas(summary_rows)
    extrinsic_rows = [v[0] for _k, v in sorted(body_cache.items())]
    return summary_rows, track_rows, extrinsic_rows


def write_report(summary_rows: list[dict], extrinsic_rows: list[dict]) -> None:
    rows = sorted(summary_rows, key=lambda r: (str(r["case"]), str(r["fusion_id"])))
    lines = []
    lines.append("# ROTO Pseudo-IMU Replay")
    lines.append("")
    lines.append(f"Generated {datetime.now(UTC).isoformat()}.")
    lines.append("")
    lines.append(
        "This is an oracle diagnostic: OptiTrack markers provide the pseudo-IMU relative-motion prior. "
        "The wand body pose is fitted from non-antenna markers, then the UWB antenna point is recovered through a fitted body-to-antenna lever arm."
    )
    lines.append("")
    lines.append("## Fusion Definitions")
    lines.append("")
    for spec in PSEUDO_SPECS:
        lines.append(f"- `{spec.fusion_id}`: {spec.description}; deployability=`{spec.deployability}`.")
    lines.append("")
    lines.append("## Lever-Arm Sanity")
    lines.append("")
    p50 = filtered.pct([r["bodyfit_antenna_residual_p50_mm"] for r in extrinsic_rows], 50)
    p95 = filtered.pct([r["bodyfit_antenna_residual_p95_mm"] for r in extrinsic_rows], 50)
    lines.append(
        f"Across capture/tag tracks, body-fit antenna residual medians are {fmt(p50)} mm P50-of-P50 and "
        f"{fmt(p95)} mm P50-of-P95. This validates that the pseudo-IMU prior is applied to the antenna point, not to the marker-body centroid."
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    cols = [
        "case_label",
        "fusion_id",
        "fusion_deployability",
        "trackmedian_err3d_p50_mm",
        "trackmedian_err3d_p95_mm",
        "sample_err3d_rmse_mm",
        "turn_center_abs_error_3d_rms_mm",
        "legacy_deltaR_error_rms_mm",
        "improvement_vs_PI0_trackmedian_err3d_p50_mm",
        "fusion_verdict",
    ]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in rows:
        vals = []
        for col in cols:
            val = row.get(col, "")
            if isinstance(val, float):
                vals.append(fmt(val, 2))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "Because the motion prior is derived from OptiTrack, PI1/PI3/PI4 are not deployable accuracy claims. "
        "They bound how much a correctly lever-armed inertial relative-motion source could help after the existing UWB position solve."
    )
    lines.append("")
    lines.append("## Output Tables")
    lines.append("")
    lines.append("- `../tables/roto_pseudo_imu_summary.csv`")
    lines.append("- `../tables/roto_pseudo_imu_per_track.csv`")
    lines.append("- `../tables/roto_pseudo_imu_extrinsics.csv`")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "ROTO_PSEUDO_IMU_REPLAY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-root", default=str(filtered.FULL_ROOT))
    parser.add_argument("--align-root", default=str(filtered.ALIGN_ROOT))
    parser.add_argument("--scale-root", default=str(filtered.SCALE_ROOT))
    parser.add_argument("--one-baseline-root", default=str(filtered.ONE_BASELINE_ROOT))
    parser.add_argument("--out-root", default=str(OUT_ROOT))
    parser.add_argument("--report-only", action="store_true", help="rewrite report from existing tables")
    args = parser.parse_args()
    filtered.configure_paths(
        full_root=args.full_root,
        align_root=args.align_root,
        scale_root=args.scale_root,
        one_baseline_root=args.one_baseline_root,
    )
    configure_output(args.out_root)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if args.report_only:
        summary_rows = pd.read_csv(TABLE_DIR / "roto_pseudo_imu_summary.csv").to_dict("records")
        extrinsic_rows = pd.read_csv(TABLE_DIR / "roto_pseudo_imu_extrinsics.csv").to_dict("records")
    else:
        summary_rows, track_rows, extrinsic_rows = run_matrix()
        write_csv(TABLE_DIR / "roto_pseudo_imu_summary.csv", summary_rows)
        write_csv(TABLE_DIR / "roto_pseudo_imu_per_track.csv", track_rows)
        write_csv(TABLE_DIR / "roto_pseudo_imu_extrinsics.csv", extrinsic_rows)
    write_report(summary_rows, extrinsic_rows)
    df = pd.DataFrame(summary_rows)
    print(f"Wrote {TABLE_DIR / 'roto_pseudo_imu_summary.csv'}")
    print(df[["case", "fusion_id", "trackmedian_err3d_p50_mm", "trackmedian_err3d_p95_mm", "fusion_verdict"]].to_string(index=False))


if __name__ == "__main__":
    main()
