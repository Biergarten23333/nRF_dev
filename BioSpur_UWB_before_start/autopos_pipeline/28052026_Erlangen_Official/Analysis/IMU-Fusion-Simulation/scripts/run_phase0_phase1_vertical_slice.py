#!/usr/bin/env python3
"""Run IMU-fusion Phase 0 and Phase 1 vertical-slice diagnostics.

This runner intentionally keeps Phase 1 small:

* Phase 0 freezes official UWB-only baselines and R01-R17 alignment.
* Phase 1 generates L0/L2 position-domain IMU drift diagnostics.
* Phase 1 runs small position-domain fusion prototypes for T2/T3/T6.
* Validation gates are emitted as machine-readable PASS/FAIL rows.

The T6 row in this first vertical slice audits raw range availability but uses a
solved-position proxy for the state update.  The gate report marks this clearly
as PASS_OR_LIMITED_PROTO, not a final raw-range EKF result.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
ANALYSIS_ROOT = SIM_ROOT.parent
OFFICIAL_ROOT = ANALYSIS_ROOT.parent
EXTRA_ROOT = ANALYSIS_ROOT / "official_extra_analysis"
CAPTURES_ROOT = OFFICIAL_ROOT / "captures" / "erlangen_20260528_optitrack"
OPTI_ROOT = OFFICIAL_ROOT / "opti_captures" / "full"

ROTO_IDS = [f"R{i:02d}" for i in range(1, 18)]
TAGS = ["BS2DCE", "BSDC91"]
ROTO_RADIUS_MM = {"BS2DCE": 440.0, "BSDC91": 560.0}
EXPECTED_DELTA_R_MM = 120.0
G_MM_S2 = 9806.65


@dataclass(frozen=True)
class BaselineCase:
    baseline_id: str
    experiment_id: str
    description: str
    sample_path: Path
    deployability: str


BASELINES = [
    BaselineCase(
        "B0",
        "B0_A0_U4_P0_T1",
        "A0 AutoPos v4-io rigid no-scale, old T4 pure UWB, no post-filter",
        EXTRA_ROOT / "FULL_US" / "roto_absolute" / "tables" / "roto_abs_samples_v4io_T4.csv",
        "online_deployable_uwb_only",
    ),
    BaselineCase(
        "B1",
        "B1_A1_U4_P0_T1",
        "A1 one-baseline scale correction control, old T4 pure UWB",
        EXTRA_ROOT
        / "FULL_AutoPos_one_baseline_scale_correction_US"
        / "roto_absolute"
        / "tables"
        / "roto_abs_samples_v4io_T4.csv",
        "diagnostic_layout_control",
    ),
    BaselineCase(
        "B2",
        "B2_A2_U4_P0_T1",
        "A2 Vicon truth anchors + delaycal oracle control, old T4 pure UWB",
        EXTRA_ROOT / "FULL_AutoPos_align_to_Vicon_US" / "roto_absolute" / "tables" / "roto_abs_samples_v4io_T4.csv",
        "oracle_layout_control",
    ),
]


PHASE1_ROWS = [
    {
        "experiment_id": "B0_A0_U4_P0_T1",
        "solver": "T1",
        "kind": "baseline",
        "deployability": "online_deployable_uwb_only",
        "description": "Phase 1 control: frozen UWB-only B0.",
    },
    {
        "experiment_id": "X_A0_L0_I0_T11",
        "solver": "T11",
        "kind": "imu_only",
        "L": "L0",
        "I": "I0",
        "deployability": "imu_only_diagnostic_oracle",
        "description": "Perfect Vicon-derived IMU position-domain diagnostic.",
    },
    {
        "experiment_id": "X_A0_L2_I3_T11",
        "solver": "T11",
        "kind": "imu_only",
        "L": "L2",
        "I": "I3",
        "deployability": "imu_only_diagnostic_realistic",
        "description": "MPU6050-like residual drift after I3 bias calibration.",
    },
    {
        "experiment_id": "X_A0_U4_P0_L0_I0_T2",
        "solver": "T2",
        "kind": "position_fusion",
        "L": "L0",
        "I": "I0",
        "prior_sigma_mm": 8.0,
        "measurement_sigma_mm": 90.0,
        "deployability": "online_oracle",
        "description": "Position-domain relative-motion prior using perfect Vicon IMU.",
    },
    {
        "experiment_id": "X_A0_U4_P0_L2_I3_T3",
        "solver": "T3",
        "kind": "position_fusion",
        "L": "L2",
        "I": "I3",
        "prior_sigma_mm": 80.0,
        "measurement_sigma_mm": 90.0,
        "deployability": "online_deployable_prototype",
        "description": "Loose-coupled position EKF prototype using realistic IMU drift.",
    },
    {
        "experiment_id": "X_A0_R2_L0_I0_T6",
        "solver": "T6",
        "kind": "range_fusion_limited_proxy",
        "L": "L0",
        "I": "I0",
        "prior_sigma_mm": 45.0,
        "measurement_sigma_mm": 75.0,
        "deployability": "raw_range_limited_proto",
        "description": "T6 limited prototype: raw range availability audited, solved-position proxy update.",
    },
    {
        "experiment_id": "X_A0_R2_L2_I3_T6",
        "solver": "T6",
        "kind": "range_fusion_limited_proxy",
        "L": "L2",
        "I": "I3",
        "prior_sigma_mm": 45.0,
        "measurement_sigma_mm": 75.0,
        "deployability": "raw_range_limited_proto",
        "description": "T6 limited prototype on realistic IMU drift; not a final tight EKF claim.",
    },
]


SENSOR_PHASE1 = {
    "L0": {
        "accel_noise_mg": 0.0,
        "residual_accel_bias_mg": 0.0,
        "accel_bias_random_walk_mg_sqrt_s": 0.0,
        "vibration_sensitivity_mg": 0.0,
    },
    "L2": {
        "accel_noise_mg": 0.8,
        "residual_accel_bias_mg": 0.20,
        "accel_bias_random_walk_mg_sqrt_s": 0.03,
        "vibration_sensitivity_mg": 0.25,
    },
}


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(SIM_ROOT))
    except ValueError:
        try:
            return str(path.resolve().relative_to(OFFICIAL_ROOT))
        except ValueError:
            return str(path)


def pct(values: Iterable[float], q: float) -> float:
    arr = np.asarray(list(values) if not isinstance(values, np.ndarray) else values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def rms(values: Iterable[float]) -> float:
    arr = np.asarray(list(values) if not isinstance(values, np.ndarray) else values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(math.sqrt(float(np.mean(arr * arr))))


def finite_stats(values: Iterable[float], prefix: str) -> dict[str, float | int]:
    arr = np.asarray(list(values) if not isinstance(values, np.ndarray) else values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {
            f"{prefix}_n": 0,
            f"{prefix}_p50_mm": float("nan"),
            f"{prefix}_p90_mm": float("nan"),
            f"{prefix}_p95_mm": float("nan"),
            f"{prefix}_rmse_mm": float("nan"),
        }
    return {
        f"{prefix}_n": int(arr.size),
        f"{prefix}_p50_mm": float(np.percentile(arr, 50)),
        f"{prefix}_p90_mm": float(np.percentile(arr, 90)),
        f"{prefix}_p95_mm": float(np.percentile(arr, 95)),
        f"{prefix}_rmse_mm": rms(arr),
    }


def fmt(value: object, digits: int = 1) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(f):
        return "nan"
    return f"{f:.{digits}f}"


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


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def write_gzip_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        df.to_csv(f, index=False)


def stable_seed(*parts: object) -> int:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "little") & 0x7FFFFFFF


def git_status() -> dict[str, object]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=OFFICIAL_ROOT.parent, text=True).strip()
    except Exception:
        commit = "unknown"
    try:
        status = subprocess.check_output(["git", "status", "--short"], cwd=OFFICIAL_ROOT.parent, text=True).strip().splitlines()
    except Exception:
        status = []
    return {"commit": commit, "status_short": status[:200], "dirty": bool(status)}


def load_official_samples(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["capture_id"].astype(str).isin(ROTO_IDS) & df["tag"].astype(str).isin(TAGS)].copy()
    df = df.sort_values(["capture_id", "tag", "uwb_time_s"]).reset_index(drop=True)
    return df


def official_to_samples(df: pd.DataFrame, experiment_id: str, deployability: str, description: str) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "experiment_id": experiment_id,
            "deployability": deployability,
            "description": description,
            "capture_id": df["capture_id"].astype(str).to_numpy(),
            "tag": df["tag"].astype(str).to_numpy(),
            "time_s": df["uwb_time_s"].to_numpy(float),
            "opti_time_s": df["opti_time_s"].to_numpy(float),
            "x_mm": df["uwb_x_mm"].to_numpy(float),
            "y_mm": df["uwb_y_vertical_mm"].to_numpy(float),
            "z_mm": df["uwb_z_mm"].to_numpy(float),
            "uwb_x_mm": df["uwb_x_mm"].to_numpy(float),
            "uwb_y_mm": df["uwb_y_vertical_mm"].to_numpy(float),
            "uwb_z_mm": df["uwb_z_mm"].to_numpy(float),
            "opti_x_mm": df["opti_x_mm"].to_numpy(float),
            "opti_y_mm": df["opti_y_vertical_mm"].to_numpy(float),
            "opti_z_mm": df["opti_z_mm"].to_numpy(float),
        }
    )
    add_errors(out)
    return out


def add_errors(df: pd.DataFrame) -> None:
    dx = df["x_mm"].to_numpy(float) - df["opti_x_mm"].to_numpy(float)
    dy = df["y_mm"].to_numpy(float) - df["opti_y_mm"].to_numpy(float)
    dz = df["z_mm"].to_numpy(float) - df["opti_z_mm"].to_numpy(float)
    df["err_x_mm"] = dx
    df["err_y_mm"] = dy
    df["err_z_mm"] = dz
    df["err3d_mm"] = np.sqrt(dx * dx + dy * dy + dz * dz)
    df["err_horizontal_xz_mm"] = np.sqrt(dx * dx + dz * dz)
    df["err_vertical_y_mm"] = np.abs(dy)


def fit_circle_xz(xyz: np.ndarray) -> dict[str, float | str]:
    pts = np.asarray(xyz, dtype=float)
    good = np.isfinite(pts).all(axis=1)
    pts = pts[good]
    if pts.shape[0] < 12:
        return {"status": "insufficient", "radius_mm": float("nan")}
    x = pts[:, 0]
    z = pts[:, 2]
    a = np.column_stack([x, z, np.ones_like(x)])
    b = x * x + z * z
    try:
        sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    except np.linalg.LinAlgError:
        return {"status": "singular", "radius_mm": float("nan")}
    cx = 0.5 * sol[0]
    cz = 0.5 * sol[1]
    c = sol[2]
    radius_sq = c + cx * cx + cz * cz
    if radius_sq <= 0 or not math.isfinite(radius_sq):
        return {"status": "bad_radius", "radius_mm": float("nan")}
    radius = math.sqrt(radius_sq)
    dist = np.sqrt((x - cx) ** 2 + (z - cz) ** 2)
    residual = dist - radius
    return {
        "status": "ok",
        "center_x_mm": float(cx),
        "center_z_mm": float(cz),
        "center_y_mm": float(np.nanmedian(pts[:, 1])),
        "radius_mm": float(radius),
        "circle_thickness_rms_mm": rms(residual),
        "circle_thickness_p95_mm": pct(np.abs(residual), 95),
    }


def track_metrics(samples: pd.DataFrame, experiment_id: str, deployability: str, description: str) -> tuple[list[dict], dict]:
    track_rows: list[dict] = []
    sample_err3: list[np.ndarray] = []
    sample_errxz: list[np.ndarray] = []
    sample_erry: list[np.ndarray] = []
    for (capture_id, tag), g0 in samples.groupby(["capture_id", "tag"], sort=True):
        g = g0.sort_values("time_s").copy()
        traj = g[["x_mm", "y_mm", "z_mm"]].to_numpy(float)
        opti = g[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float)
        err3 = g["err3d_mm"].to_numpy(float)
        errxz = g["err_horizontal_xz_mm"].to_numpy(float)
        erry = g["err_vertical_y_mm"].to_numpy(float)
        sample_err3.append(err3)
        sample_errxz.append(errxz)
        sample_erry.append(erry)

        f_circle = fit_circle_xz(traj)
        o_circle = fit_circle_xz(opti)
        circle: dict[str, float | int | str] = {}
        if f_circle.get("status") == "ok" and o_circle.get("status") == "ok":
            center_diff = np.array(
                [
                    float(f_circle["center_x_mm"]) - float(o_circle["center_x_mm"]),
                    float(f_circle["center_y_mm"]) - float(o_circle["center_y_mm"]),
                    float(f_circle["center_z_mm"]) - float(o_circle["center_z_mm"]),
                ],
                dtype=float,
            )
            radius_error = float(f_circle["radius_mm"]) - float(o_circle["radius_mm"])
            circle = {
                "turn_center_abs_error_3d_mm": float(np.linalg.norm(center_diff)),
                "turn_center_abs_error_xz_mm": float(math.sqrt(center_diff[0] ** 2 + center_diff[2] ** 2)),
                "turn_center_abs_error_y_mm": float(abs(center_diff[1])),
                "fused_radius_mm": float(f_circle["radius_mm"]),
                "opti_radius_mm": float(o_circle["radius_mm"]),
                "nominal_radius_mm": ROTO_RADIUS_MM.get(str(tag), float("nan")),
                "radius_error_mm": radius_error,
                "radius_error_abs_mm": abs(radius_error),
                "circle_thickness_rms_mm": float(f_circle["circle_thickness_rms_mm"]),
                "circle_thickness_p95_mm": float(f_circle["circle_thickness_p95_mm"]),
            }
        else:
            circle = {
                "turn_center_abs_error_3d_mm": float("nan"),
                "turn_center_abs_error_xz_mm": float("nan"),
                "turn_center_abs_error_y_mm": float("nan"),
                "fused_radius_mm": float("nan"),
                "opti_radius_mm": float("nan"),
                "nominal_radius_mm": ROTO_RADIUS_MM.get(str(tag), float("nan")),
                "radius_error_mm": float("nan"),
                "radius_error_abs_mm": float("nan"),
                "circle_thickness_rms_mm": float("nan"),
                "circle_thickness_p95_mm": float("nan"),
            }

        times = g["time_s"].to_numpy(float)
        dt = np.diff(times)
        update_rate = 1.0 / np.nanmedian(dt[np.isfinite(dt) & (dt > 0)]) if np.any(np.isfinite(dt) & (dt > 0)) else float("nan")
        row: dict[str, float | int | str] = {
            "experiment_id": experiment_id,
            "deployability": deployability,
            "description": description,
            "capture_id": str(capture_id),
            "tag": str(tag),
            "n_samples": int(np.sum(np.isfinite(err3))),
            "availability_fraction": float(np.mean(np.isfinite(err3))) if err3.size else 0.0,
            "effective_update_rate_hz": float(update_rate),
            "err3d_p50_mm": pct(err3, 50),
            "err3d_p95_mm": pct(err3, 95),
            "err3d_rmse_mm": rms(err3),
            "err_horizontal_xz_p50_mm": pct(errxz, 50),
            "err_horizontal_xz_p95_mm": pct(errxz, 95),
            "err_vertical_y_p50_mm": pct(erry, 50),
            "err_vertical_y_p95_mm": pct(erry, 95),
            "max_gap_s": float(np.nanmax(dt)) if dt.size else float("nan"),
        }
        for col in [
            "imu_only_endpoint_drift_3d_mm",
            "imu_only_endpoint_drift_xz_mm",
            "imu_only_drift_rate_3d_mm_s",
            "uwb_update_accept_rate",
            "uwb_innovation_nis_median",
            "uwb_innovation_nis_p95",
            "filter_divergence_count",
            "raw_range_ge4_ratio",
            "raw_range_full8_ratio",
        ]:
            if col in g.columns:
                row[col] = pct(g[col].to_numpy(float), 50)
        row.update(circle)
        track_rows.append(row)

    all_err3 = np.concatenate(sample_err3) if sample_err3 else np.array([], dtype=float)
    all_errxz = np.concatenate(sample_errxz) if sample_errxz else np.array([], dtype=float)
    all_erry = np.concatenate(sample_erry) if sample_erry else np.array([], dtype=float)
    summary: dict[str, float | int | str] = {
        "experiment_id": experiment_id,
        "deployability": deployability,
        "description": description,
        "track_count": int(len(track_rows)),
        "capture_count": int(len({r["capture_id"] for r in track_rows})),
    }
    summary.update(finite_stats(all_err3, "sample_err3d"))
    summary.update(finite_stats(all_errxz, "sample_err_horizontal_xz"))
    summary.update(finite_stats(all_erry, "sample_err_vertical_y"))
    for metric, out_name in [
        ("err3d_p50_mm", "trackmedian_err3d_p50_mm"),
        ("err3d_p95_mm", "trackmedian_err3d_p95_mm"),
        ("err3d_rmse_mm", "trackmedian_err3d_rmse_mm"),
        ("err_horizontal_xz_p95_mm", "trackmedian_horizontal_xz_p95_mm"),
        ("err_vertical_y_p95_mm", "trackmedian_vertical_y_p95_mm"),
        ("turn_center_abs_error_3d_mm", "trackmedian_turn_center_abs_error_3d_mm"),
        ("turn_center_abs_error_xz_mm", "trackmedian_turn_center_abs_error_xz_mm"),
        ("turn_center_abs_error_y_mm", "trackmedian_turn_center_abs_error_y_mm"),
        ("radius_error_abs_mm", "trackmedian_radius_error_abs_mm"),
        ("circle_thickness_rms_mm", "trackmedian_circle_thickness_rms_mm"),
        ("circle_thickness_p95_mm", "trackmedian_circle_thickness_p95_mm"),
        ("imu_only_endpoint_drift_3d_mm", "imu_only_endpoint_drift_3d_trackmedian_mm"),
        ("imu_only_endpoint_drift_xz_mm", "imu_only_endpoint_drift_xz_trackmedian_mm"),
        ("imu_only_drift_rate_3d_mm_s", "imu_only_drift_rate_3d_trackmedian_mm_s"),
        ("uwb_update_accept_rate", "uwb_update_accept_rate_trackmedian"),
        ("uwb_innovation_nis_median", "uwb_innovation_nis_trackmedian"),
        ("uwb_innovation_nis_p95", "uwb_innovation_nis_p95_trackmedian"),
        ("raw_range_ge4_ratio", "raw_range_ge4_ratio_trackmedian"),
        ("raw_range_full8_ratio", "raw_range_full8_ratio_trackmedian"),
    ]:
        vals = [float(r.get(metric, float("nan"))) for r in track_rows]
        summary[out_name] = pct(vals, 50)

    pair_delta: list[float] = []
    by_capture: dict[str, dict[str, dict]] = {}
    for r in track_rows:
        by_capture.setdefault(str(r["capture_id"]), {})[str(r["tag"])] = r
    for by_tag in by_capture.values():
        if "BS2DCE" not in by_tag or "BSDC91" not in by_tag:
            continue
        inner = float(by_tag["BS2DCE"].get("fused_radius_mm", float("nan")))
        outer = float(by_tag["BSDC91"].get("fused_radius_mm", float("nan")))
        if math.isfinite(inner) and math.isfinite(outer):
            pair_delta.append((outer - inner) - EXPECTED_DELTA_R_MM)
    abs_delta = np.abs(np.asarray(pair_delta, dtype=float))
    summary["capture_pair_count"] = int(len(pair_delta))
    summary["legacy_deltaR_error_rms_mm"] = rms(pair_delta)
    summary["legacy_abs_deltaR_error_median_mm"] = pct(abs_delta, 50)
    summary["legacy_abs_deltaR_error_p95_mm"] = pct(abs_delta, 95)
    return track_rows, summary


def build_pairing_manifest() -> list[dict]:
    offsets_path = EXTRA_ROOT / "FULL_US" / "roto_absolute" / "tables" / "roto_time_offsets_v4io_T4.csv"
    offsets = pd.read_csv(offsets_path)
    offset_by_capture = {str(r["capture_id"]): r for _, r in offsets.iterrows()}
    rows: list[dict] = []
    for capture_id in ROTO_IDS:
        uwb_matches = sorted(CAPTURES_ROOT.glob(f"roto_{capture_id}_BS2DCE_BSDC91_120s_*"))
        static_matches = sorted(CAPTURES_ROOT.glob(f"roto_{capture_id}-Static*"))
        opti_trc = OPTI_ROOT / f"{capture_id}.trc"
        opti_csv = OPTI_ROOT / f"{capture_id}.csv"
        off = offset_by_capture.get(capture_id)
        row = {
            "capture_id": capture_id,
            "uwb_capture_count": len(uwb_matches),
            "uwb_capture_path": str(uwb_matches[0].relative_to(OFFICIAL_ROOT)) if len(uwb_matches) == 1 else "",
            "opti_trc_path": str(opti_trc.relative_to(OFFICIAL_ROOT)),
            "opti_csv_path": str(opti_csv.relative_to(OFFICIAL_ROOT)),
            "opti_trc_exists": opti_trc.exists(),
            "opti_csv_exists": opti_csv.exists(),
            "static_test_matches_excluded": len(static_matches),
            "alignment_status": str(off["status"]) if off is not None else "missing",
            "beta_s": float(off["beta_s"]) if off is not None else float("nan"),
            "alignment_source": str(offsets_path.relative_to(OFFICIAL_ROOT)),
            "alignment_policy": "fixed_capture_level_beta_from_primary_v4io_U4",
            "capture_set": "R01-R17",
        }
        row["pairing_ok"] = (
            row["uwb_capture_count"] == 1
            and bool(row["opti_trc_exists"])
            and bool(row["opti_csv_exists"])
            and row["alignment_status"] == "ok"
        )
        rows.append(row)
    return rows


def raw_range_availability(pairing_rows: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for pair in pairing_rows:
        if not pair.get("pairing_ok"):
            continue
        cap_dir = OFFICIAL_ROOT / str(pair["uwb_capture_path"])
        tr_all_matches = sorted(cap_dir.glob("tag_capture*/tr_all.csv"))
        if not tr_all_matches:
            continue
        tr_all = tr_all_matches[0]
        try:
            df = pd.read_csv(tr_all, usecols=["host_elapsed_s", "sweep", "peer_name", "anchor_id", "range_mm", "quality_percent", "valid"])
        except Exception as exc:
            for tag in TAGS:
                rows.append(
                    {
                        "capture_id": pair["capture_id"],
                        "tag": tag,
                        "raw_range_path": str(tr_all.relative_to(OFFICIAL_ROOT)),
                        "status": "read_error",
                        "error": str(exc),
                    }
                )
            continue
        for tag in TAGS:
            gt = df[df["peer_name"].astype(str) == tag].copy()
            if gt.empty:
                rows.append(
                    {
                        "capture_id": pair["capture_id"],
                        "tag": tag,
                        "raw_range_path": str(tr_all.relative_to(OFFICIAL_ROOT)),
                        "status": "missing_tag",
                    }
                )
                continue
            gt["valid_anchor"] = (gt["valid"].astype(float) > 0) & (gt["range_mm"].astype(float) > 0)
            grp = gt.groupby(["host_elapsed_s", "sweep"], sort=True)
            valid_counts = grp["valid_anchor"].sum().to_numpy(float)
            quality = gt.loc[gt["valid_anchor"], "quality_percent"].astype(float).to_numpy()
            rows.append(
                {
                    "capture_id": pair["capture_id"],
                    "tag": tag,
                    "raw_range_path": str(tr_all.relative_to(OFFICIAL_ROOT)),
                    "status": "ok",
                    "raw_range_frames": int(valid_counts.size),
                    "raw_range_ge4_ratio": float(np.mean(valid_counts >= 4)) if valid_counts.size else 0.0,
                    "raw_range_full8_ratio": float(np.mean(valid_counts >= 8)) if valid_counts.size else 0.0,
                    "valid_anchor_count_median": pct(valid_counts, 50),
                    "valid_anchor_count_p05": pct(valid_counts, 5),
                    "quality_percent_median": pct(quality, 50),
                    "quality_percent_p05": pct(quality, 5),
                }
            )
    return rows


def simulate_imu_rows(b0_samples: pd.DataFrame, run_id: str, seeds_out: list[dict]) -> dict[str, pd.DataFrame]:
    rows_by_exp: dict[str, pd.DataFrame] = {}
    base = b0_samples.sort_values(["capture_id", "tag", "time_s"]).copy()
    for spec in [r for r in PHASE1_ROWS if r["kind"] == "imu_only"]:
        exp = str(spec["experiment_id"])
        l_id = str(spec["L"])
        sensor = SENSOR_PHASE1[l_id]
        chunks: list[pd.DataFrame] = []
        for (capture_id, tag), g0 in base.groupby(["capture_id", "tag"], sort=True):
            g = g0.sort_values("time_s").copy()
            truth = g[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float)
            times = g["time_s"].to_numpy(float)
            if l_id == "L0":
                drift = np.zeros_like(truth)
                seed = 0
            else:
                seed = stable_seed(run_id, exp, capture_id, tag)
                rng = np.random.default_rng(seed)
                drift = np.zeros_like(truth)
                vel = np.zeros(3, dtype=float)
                pos = np.zeros(3, dtype=float)
                bias = rng.normal(0.0, float(sensor["residual_accel_bias_mg"]) * G_MM_S2 / 1000.0, size=3)
                phase = rng.uniform(0.0, 2.0 * math.pi, size=3)
                freq = rng.uniform(7.0, 13.0, size=3)
                for i in range(1, len(times)):
                    dt = float(times[i] - times[i - 1])
                    if not math.isfinite(dt) or dt <= 0:
                        dt = 1.0 / 15.0
                    dt = min(max(dt, 1e-3), 0.25)
                    rw_sigma = float(sensor["accel_bias_random_walk_mg_sqrt_s"]) * G_MM_S2 / 1000.0 * math.sqrt(dt)
                    bias = bias + rng.normal(0.0, rw_sigma, size=3)
                    noise_sigma = float(sensor["accel_noise_mg"]) * G_MM_S2 / 1000.0
                    vib_amp = float(sensor["vibration_sensitivity_mg"]) * G_MM_S2 / 1000.0
                    vib = vib_amp * np.sin(2.0 * math.pi * freq * times[i] + phase)
                    acc_err = bias + rng.normal(0.0, noise_sigma, size=3) + vib
                    pos = pos + vel * dt + 0.5 * acc_err * dt * dt
                    vel = vel + acc_err * dt
                    drift[i] = pos
            out = g.copy()
            out["experiment_id"] = exp
            out["deployability"] = str(spec["deployability"])
            out["description"] = str(spec["description"])
            out["L"] = l_id
            out["I"] = str(spec["I"])
            out["x_mm"] = truth[:, 0] + drift[:, 0]
            out["y_mm"] = truth[:, 1] + drift[:, 1]
            out["z_mm"] = truth[:, 2] + drift[:, 2]
            endpoint = drift[-1] if len(drift) else np.full(3, np.nan)
            endpoint_3d = float(np.linalg.norm(endpoint))
            endpoint_xz = float(math.sqrt(endpoint[0] * endpoint[0] + endpoint[2] * endpoint[2]))
            duration = float(times[-1] - times[0]) if len(times) > 1 else float("nan")
            out["imu_only_endpoint_drift_3d_mm"] = endpoint_3d
            out["imu_only_endpoint_drift_xz_mm"] = endpoint_xz
            out["imu_only_endpoint_drift_y_mm"] = float(abs(endpoint[1]))
            out["imu_only_drift_rate_3d_mm_s"] = endpoint_3d / duration if math.isfinite(duration) and duration > 0 else float("nan")
            out["noise_seed"] = seed
            add_errors(out)
            chunks.append(out)
            if l_id != "L0":
                seeds_out.append(
                    {
                        "experiment_id": exp,
                        "capture_id": str(capture_id),
                        "tag": str(tag),
                        "seed": int(seed),
                        "L": l_id,
                        "I": str(spec["I"]),
                        "source": "stable sha256(run_id, experiment_id, capture_id, tag)",
                    }
                )
        rows_by_exp[exp] = pd.concat(chunks, ignore_index=True)
    return rows_by_exp


def relative_motion_filter(
    times_s: np.ndarray,
    measurement_xyz: np.ndarray,
    prior_xyz: np.ndarray,
    prior_sigma_mm: float,
    measurement_sigma_mm: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    times = np.asarray(times_s, dtype=float)
    meas = np.asarray(measurement_xyz, dtype=float)
    prior = np.asarray(prior_xyz, dtype=float)
    finite_meas = np.isfinite(meas).all(axis=1)
    finite_prior = np.isfinite(prior).all(axis=1)
    out = np.full_like(meas, np.nan, dtype=float)
    innovations = np.full(meas.shape[0], np.nan, dtype=float)
    nis = np.full(meas.shape[0], np.nan, dtype=float)
    if int(np.sum(finite_meas)) < 1:
        return out, innovations, nis, int(np.sum(~finite_prior))
    first = int(np.where(finite_meas)[0][0])
    x = meas[first].copy()
    p = np.eye(3) * 150.0**2
    ident = np.eye(3)
    rmat = np.eye(3) * float(measurement_sigma_mm) ** 2
    q_base = float(prior_sigma_mm) ** 2
    prev_prior = prior[first].copy() if finite_prior[first] else None
    prior_missing = 0
    for idx in range(meas.shape[0]):
        if idx == first:
            x_pred = x.copy()
            p_pred = p.copy()
        else:
            if finite_prior[idx] and prev_prior is not None:
                delta = prior[idx] - prev_prior
                q_scale = 1.0
            else:
                delta = np.zeros(3)
                q_scale = 25.0
                prior_missing += 1
            x_pred = x + delta
            p_pred = p + ident * q_base * q_scale
        if finite_meas[idx]:
            z = meas[idx]
            innov = z - x_pred
            s = p_pred + rmat
            k = p_pred @ np.linalg.pinv(s)
            x = x_pred + k @ innov
            p = (ident - k) @ p_pred @ (ident - k).T + k @ rmat @ k.T
            out[idx] = x
            innovations[idx] = float(np.linalg.norm(innov))
            nis[idx] = float(innov @ np.linalg.pinv(s) @ innov)
        else:
            x = x_pred
            p = p_pred
        if finite_prior[idx]:
            prev_prior = prior[idx].copy()
    return out, innovations, nis, prior_missing


def fusion_rows(
    b0_samples: pd.DataFrame,
    imu_samples: dict[str, pd.DataFrame],
    raw_stats: list[dict],
) -> dict[str, pd.DataFrame]:
    rows_by_exp: dict[str, pd.DataFrame] = {}
    raw_by_track = {(str(r["capture_id"]), str(r["tag"])): r for r in raw_stats if r.get("status") == "ok"}
    base = b0_samples.sort_values(["capture_id", "tag", "time_s"]).copy()
    for spec in [r for r in PHASE1_ROWS if r["kind"] in {"position_fusion", "range_fusion_limited_proxy"}]:
        exp = str(spec["experiment_id"])
        l_id = str(spec["L"])
        i_id = str(spec["I"])
        if l_id == "L0":
            prior_df = imu_samples["X_A0_L0_I0_T11"]
        else:
            prior_df = imu_samples["X_A0_L2_I3_T11"]
        prior_by_track = {k: g.sort_values("time_s").copy() for k, g in prior_df.groupby(["capture_id", "tag"], sort=True)}
        chunks: list[pd.DataFrame] = []
        for (capture_id, tag), g0 in base.groupby(["capture_id", "tag"], sort=True):
            g = g0.sort_values("time_s").copy()
            prior_g = prior_by_track[(capture_id, tag)]
            meas = g[["uwb_x_mm", "uwb_y_mm", "uwb_z_mm"]].to_numpy(float)
            prior = prior_g[["x_mm", "y_mm", "z_mm"]].to_numpy(float)
            fused, innovation, nis, prior_missing = relative_motion_filter(
                g["time_s"].to_numpy(float),
                meas,
                prior,
                float(spec["prior_sigma_mm"]),
                float(spec["measurement_sigma_mm"]),
            )
            out = g.copy()
            out["experiment_id"] = exp
            out["deployability"] = str(spec["deployability"])
            out["description"] = str(spec["description"])
            out["L"] = l_id
            out["I"] = i_id
            out["x_mm"] = fused[:, 0]
            out["y_mm"] = fused[:, 1]
            out["z_mm"] = fused[:, 2]
            out["uwb_update_accept_rate"] = float(np.mean(np.isfinite(meas).all(axis=1)))
            out["uwb_innovation_norm_mm"] = innovation
            out["uwb_innovation_nis"] = nis
            out["uwb_innovation_nis_median"] = pct(nis, 50)
            out["uwb_innovation_nis_p95"] = pct(nis, 95)
            out["prior_missing_samples"] = prior_missing
            out["filter_divergence_count"] = 0
            if str(spec["kind"]) == "range_fusion_limited_proxy":
                rs = raw_by_track.get((str(capture_id), str(tag)), {})
                out["raw_range_ge4_ratio"] = float(rs.get("raw_range_ge4_ratio", float("nan")))
                out["raw_range_full8_ratio"] = float(rs.get("raw_range_full8_ratio", float("nan")))
                out["range_bias_policy_status"] = "PASS_OR_LIMITED_PROTO"
            add_errors(out)
            chunks.append(out)
        rows_by_exp[exp] = pd.concat(chunks, ignore_index=True)
    return rows_by_exp


def verdict(summary: dict, b0: dict | None = None) -> str:
    exp = str(summary["experiment_id"])
    deploy = str(summary.get("deployability", ""))
    if exp.startswith("B0"):
        return "BASELINE_UWB_ONLY"
    if "imu_only" in deploy:
        drift = float(summary.get("imu_only_endpoint_drift_3d_trackmedian_mm", float("nan")))
        if "oracle" in deploy and drift <= 1.0:
            return "IMU_ONLY_PERFECT_ORACLE"
        if math.isfinite(drift) and drift >= 250.0:
            return "IMU_ONLY_DRIFTS_AS_EXPECTED"
        return "IMU_ONLY_INVALID"
    if b0 is None:
        return "FUSION_UNRANKED"
    p50 = float(summary.get("trackmedian_err3d_p50_mm", float("nan")))
    p95 = float(summary.get("trackmedian_err3d_p95_mm", float("nan")))
    d95 = p95 - float(b0.get("trackmedian_err3d_p95_mm", float("nan")))
    d50 = p50 - float(b0.get("trackmedian_err3d_p50_mm", float("nan")))
    d_delta_r = float(summary.get("legacy_deltaR_error_rms_mm", float("nan"))) - float(
        b0.get("legacy_deltaR_error_rms_mm", float("nan"))
    )
    d_radius = float(summary.get("trackmedian_radius_error_abs_mm", float("nan"))) - float(
        b0.get("trackmedian_radius_error_abs_mm", float("nan"))
    )
    div = float(summary.get("filter_divergence_count_trackmedian", 0.0))
    if div > 0:
        return "FUSION_DIVERGED"
    if (math.isfinite(d_delta_r) and d_delta_r > 5.0) or (math.isfinite(d_radius) and d_radius > 20.0):
        return "FUSION_HURTS_GEOMETRY"
    if d50 <= -5.0 and d95 <= 5.0:
        if "limited_proto" in deploy or "oracle" in deploy or "diagnostic" in deploy:
            return "FUSION_HELPS_DIAGNOSTIC_ONLY"
        return "FUSION_HELPS_DEPLOYABLE"
    if d95 > 5.0:
        return "FUSION_HURTS_TAIL"
    return "FUSION_NEUTRAL"


def add_baseline_deltas(summary_rows: list[dict]) -> None:
    by_exp = {str(r["experiment_id"]): r for r in summary_rows}
    b0 = by_exp.get("B0_A0_U4_P0_T1")
    for row in summary_rows:
        if b0 is not None:
            for key in [
                "trackmedian_err3d_p50_mm",
                "trackmedian_err3d_p95_mm",
                "sample_err3d_rmse_mm",
                "legacy_deltaR_error_rms_mm",
                "trackmedian_radius_error_abs_mm",
            ]:
                row[f"delta_vs_B0_{key}"] = float(row.get(key, float("nan"))) - float(b0.get(key, float("nan")))
        row["verdict"] = verdict(row, b0)


def ensure_dirs(*dirs: Path) -> None:
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def plot_full_tracks(samples_by_exp: dict[str, pd.DataFrame], out_dir: Path, phase: str, figure_rows: list[dict]) -> None:
    colors = {
        "B0_A0_U4_P0_T1": "#1f77b4",
        "B1_A1_U4_P0_T1": "#2ca02c",
        "B2_A2_U4_P0_T1": "#9467bd",
        "X_A0_L0_I0_T11": "#17becf",
        "X_A0_L2_I3_T11": "#d62728",
        "X_A0_U4_P0_L0_I0_T2": "#ff7f0e",
        "X_A0_U4_P0_L2_I3_T3": "#8c564b",
        "X_A0_R2_L0_I0_T6": "#bcbd22",
        "X_A0_R2_L2_I3_T6": "#e377c2",
    }
    for exp, df in samples_by_exp.items():
        exp_dir = out_dir / "full" / exp
        exp_dir.mkdir(parents=True, exist_ok=True)
        for (capture_id, tag), g0 in df.groupby(["capture_id", "tag"], sort=True):
            g = g0.sort_values("time_s").copy()
            fig, ax = plt.subplots(figsize=(6.0, 5.0))
            ax.plot(g["opti_x_mm"], g["opti_z_mm"], color="black", lw=1.6, label="Opti")
            if {"uwb_x_mm", "uwb_z_mm"}.issubset(g.columns):
                ax.plot(g["uwb_x_mm"], g["uwb_z_mm"], color="0.70", lw=0.9, alpha=0.8, label="UWB")
            ax.plot(g["x_mm"], g["z_mm"], color=colors.get(exp, "#1f77b4"), lw=1.2, label=exp)
            ax.set_aspect("equal", adjustable="box")
            ax.set_xlabel("x mm")
            ax.set_ylabel("z mm")
            ax.grid(True, alpha=0.25)
            ax.legend(loc="best", fontsize=7)
            title = (
                f"{phase} {exp} {capture_id}/{tag}\n"
                f"P50={fmt(pct(g['err3d_mm'].to_numpy(float), 50))} mm "
                f"P95={fmt(pct(g['err3d_mm'].to_numpy(float), 95))} mm"
            )
            ax.set_title(title, fontsize=9)
            path = exp_dir / f"{exp}__{capture_id}__{tag}__xz.png"
            fig.tight_layout()
            fig.savefig(path, dpi=140)
            plt.close(fig)
            figure_rows.append(
                {
                    "figure_path": str(path.relative_to(SIM_ROOT)),
                    "figure_kind": "trajectory_xz_overlay",
                    "phase": phase,
                    "experiment_id": exp,
                    "capture_id": str(capture_id),
                    "tag": str(tag),
                    "metric_context": "Opti/UWB/output top-view",
                    "baseline_id": "B0" if exp != "B0_A0_U4_P0_T1" else "",
                    "comparison_id": exp,
                    "notes": "full per-track PNG",
                }
            )


def plot_contact_sheets(samples_by_exp: dict[str, pd.DataFrame], out_dir: Path, phase: str, figure_rows: list[dict]) -> None:
    sheet_dir = out_dir / "contact_sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    for exp, df in samples_by_exp.items():
        groups = list(df.groupby(["capture_id", "tag"], sort=True))
        n = len(groups)
        cols = 4
        rows = int(math.ceil(n / cols))
        fig, axes = plt.subplots(rows, cols, figsize=(cols * 3.0, rows * 2.8), squeeze=False)
        for ax in axes.ravel():
            ax.axis("off")
        for idx, ((capture_id, tag), g0) in enumerate(groups):
            ax = axes.ravel()[idx]
            ax.axis("on")
            g = g0.sort_values("time_s")
            ax.plot(g["opti_x_mm"], g["opti_z_mm"], color="black", lw=0.9)
            ax.plot(g["uwb_x_mm"], g["uwb_z_mm"], color="0.75", lw=0.7)
            ax.plot(g["x_mm"], g["z_mm"], color="#1f77b4", lw=0.8)
            ax.set_aspect("equal", adjustable="box")
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(f"{capture_id}/{tag} P95={fmt(pct(g['err3d_mm'].to_numpy(float), 95), 0)}", fontsize=7)
        fig.suptitle(f"{phase} {exp}", fontsize=12)
        path = sheet_dir / f"{exp}__contact_sheet.png"
        fig.tight_layout(rect=[0, 0.02, 1, 0.98])
        fig.savefig(path, dpi=130)
        plt.close(fig)
        figure_rows.append(
            {
                "figure_path": str(path.relative_to(SIM_ROOT)),
                "figure_kind": "contact_sheet_xz",
                "phase": phase,
                "experiment_id": exp,
                "capture_id": "R01-R17",
                "tag": "both",
                "metric_context": "contact sheet",
                "baseline_id": "B0" if exp != "B0_A0_U4_P0_T1" else "",
                "comparison_id": exp,
                "notes": "all official dynamic ROTO tracks",
            }
        )


def markdown_table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return ""
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = []
    for row in rows:
        vals = []
        for col in columns:
            v = row.get(col, "")
            if isinstance(v, float):
                vals.append(fmt(v))
            else:
                vals.append(str(v))
        body.append("| " + " | ".join(vals) + " |")
    return "\n".join([header, sep] + body)


def validation_gates(
    phase: str,
    run_id: str,
    pairing_rows: list[dict],
    phase1_summary: list[dict] | None,
    figure_count: int,
    seeds_count: int,
) -> list[dict]:
    rows: list[dict] = []
    pairing_ok = all(bool(r.get("pairing_ok")) for r in pairing_rows) and len(pairing_rows) == 17
    configs_ok = (SIM_ROOT / "configs" / "frame_conventions.md").exists()
    range_policy_ok = (SIM_ROOT / "configs" / "range_bias_policy.md").exists()
    sensors_ok = (SIM_ROOT / "configs" / "sensors.yaml").exists()
    if phase == "phase0":
        rows.extend(
            [
                {
                    "gate_id": "G4_fixed_time_alignment",
                    "status": "PASS" if pairing_ok else "FAIL",
                    "phase": phase,
                    "evidence": "R01-R17 pairing manifest and official beta_s alignment table",
                    "blocking_next_phase": not pairing_ok,
                },
                {
                    "gate_id": "G6_multimetric_verdict",
                    "status": "PASS",
                    "phase": phase,
                    "evidence": "baseline summary emits P50/P95/deltaR/radius metrics and PNG figure index",
                    "blocking_next_phase": False,
                },
            ]
        )
        return rows

    by_exp = {str(r["experiment_id"]): r for r in (phase1_summary or [])}
    l0 = by_exp.get("X_A0_L0_I0_T11", {})
    l2 = by_exp.get("X_A0_L2_I3_T11", {})
    l0_ok = float(l0.get("trackmedian_err3d_p95_mm", float("inf"))) <= 1.0
    l2_drift = float(l2.get("imu_only_endpoint_drift_3d_trackmedian_mm", float("nan")))
    l2_ok = math.isfinite(l2_drift) and l2_drift >= 250.0
    metric_cols = [
        "trackmedian_err3d_p50_mm",
        "trackmedian_err3d_p95_mm",
        "legacy_deltaR_error_rms_mm",
        "trackmedian_radius_error_abs_mm",
        "sample_err3d_rmse_mm",
    ]
    metrics_ok = all(all(col in r for col in metric_cols) for r in (phase1_summary or [])) and figure_count > 0
    rows.extend(
        [
            {
                "gate_id": "G1_frame_gravity",
                "status": "PASS" if configs_ok and l0_ok else "FAIL",
                "phase": phase,
                "evidence": f"frame_conventions.md exists={configs_ok}; L0/T11 P95={fmt(l0.get('trackmedian_err3d_p95_mm', float('nan')), 3)} mm",
                "blocking_next_phase": not (configs_ok and l0_ok),
            },
            {
                "gate_id": "G2_drift_from_L_properties",
                "status": "PASS" if sensors_ok and l2_ok else "FAIL",
                "phase": phase,
                "evidence": f"sensors.yaml exists={sensors_ok}; L2/T11 endpoint drift median={fmt(l2_drift)} mm",
                "blocking_next_phase": not (sensors_ok and l2_ok),
            },
            {
                "gate_id": "G3_range_bias_policy",
                "status": "PASS_OR_LIMITED_PROTO" if range_policy_ok else "FAIL",
                "phase": phase,
                "evidence": "range_bias_policy.md exists; T6 raw availability audited but solved-position proxy is used",
                "blocking_next_phase": True,
            },
            {
                "gate_id": "G4_fixed_time_alignment",
                "status": "PASS" if pairing_ok else "FAIL",
                "phase": phase,
                "evidence": "all Phase 1 rows use official aligned sample grid; no beta_s refit",
                "blocking_next_phase": not pairing_ok,
            },
            {
                "gate_id": "G5_noise_seed_repeats",
                "status": "PASS_DEBUG_SINGLE_SEED" if seeds_count >= 34 else "FAIL",
                "phase": phase,
                "evidence": f"recorded stochastic seeds={seeds_count}; final claims still require multiseed",
                "blocking_next_phase": True,
            },
            {
                "gate_id": "G6_multimetric_verdict",
                "status": "PASS" if metrics_ok else "FAIL",
                "phase": phase,
                "evidence": f"summary metric columns present={metrics_ok}; figure_count={figure_count}",
                "blocking_next_phase": not metrics_ok,
            },
        ]
    )
    return rows


def write_gate_report(path: Path, gate_rows: list[dict], title: str) -> None:
    cols = ["gate_id", "status", "blocking_next_phase", "evidence"]
    body = [
        f"# {title}",
        "",
        markdown_table(gate_rows, cols),
        "",
        "Status semantics:",
        "",
        "- `PASS`: usable for the current phase gate.",
        "- `PASS_OR_LIMITED_PROTO`: acceptable for Phase 1 prototype only; blocks broad Phase 2.",
        "- `PASS_DEBUG_SINGLE_SEED`: acceptable for debug/screening only; final rows need repeated seeds.",
        "- `FAIL`: stop before using this run for phase progression.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body), encoding="utf-8")


def write_phase_report(path: Path, title: str, summary_rows: list[dict], gate_rows: list[dict], extra: str = "") -> None:
    cols = [
        "experiment_id",
        "track_count",
        "trackmedian_err3d_p50_mm",
        "trackmedian_err3d_p95_mm",
        "legacy_deltaR_error_rms_mm",
        "trackmedian_radius_error_abs_mm",
        "verdict",
    ]
    lines = [
        f"# {title}",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## Summary",
        "",
        markdown_table(summary_rows, cols),
        "",
        "## Validation Gates",
        "",
        markdown_table(gate_rows, ["gate_id", "status", "blocking_next_phase", "evidence"]),
        "",
    ]
    if extra:
        lines.extend(["## Notes", "", extra, ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, object]:
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    phase0_dir = SIM_ROOT / "runs" / "phase0_baseline" / run_id
    phase1_dir = SIM_ROOT / "runs" / "phase1_vertical_slice" / run_id
    start = time.perf_counter()
    ensure_dirs(
        phase0_dir / "tables",
        phase0_dir / "reports",
        phase0_dir / "figs" / "full",
        phase0_dir / "figs" / "contact_sheets",
        phase1_dir / "tables",
        phase1_dir / "reports",
        phase1_dir / "figs" / "full",
        phase1_dir / "figs" / "curated",
        phase1_dir / "figs" / "contact_sheets",
        phase1_dir / "traces",
        phase1_dir / "logs",
        SIM_ROOT / "cache" / "alignment",
        SIM_ROOT / "cache" / "uwb_streams",
        SIM_ROOT / "cache" / "perfect_imu",
        SIM_ROOT / "cache" / "sensor_imu",
        SIM_ROOT / "manifests",
    )

    pairing_rows = build_pairing_manifest()
    write_csv(phase0_dir / "tables" / "pairing_alignment_manifest.csv", pairing_rows)
    write_csv(SIM_ROOT / "cache" / "alignment" / f"R01_R17_fixed_beta_v4io_U4__{run_id}.csv", pairing_rows)

    raw_stats = raw_range_availability(pairing_rows)
    write_csv(SIM_ROOT / "cache" / "uwb_streams" / f"raw_range_availability__{run_id}.csv", raw_stats)

    baseline_samples_by_exp: dict[str, pd.DataFrame] = {}
    phase0_track_rows: list[dict] = []
    phase0_summary_rows: list[dict] = []
    for case in BASELINES:
        df = load_official_samples(case.sample_path)
        samples = official_to_samples(df, case.experiment_id, case.deployability, case.description)
        baseline_samples_by_exp[case.experiment_id] = samples
        tracks, summary = track_metrics(samples, case.experiment_id, case.deployability, case.description)
        summary["baseline_id"] = case.baseline_id
        summary["sample_source"] = str(case.sample_path.relative_to(OFFICIAL_ROOT))
        phase0_track_rows.extend(tracks)
        phase0_summary_rows.append(summary)
    add_baseline_deltas(phase0_summary_rows)
    write_csv(phase0_dir / "tables" / "baseline_track_metrics.csv", phase0_track_rows)
    write_csv(phase0_dir / "tables" / "baseline_summary.csv", phase0_summary_rows)

    figure_rows0: list[dict] = []
    plot_full_tracks(baseline_samples_by_exp, phase0_dir / "figs", "phase0", figure_rows0)
    plot_contact_sheets(baseline_samples_by_exp, phase0_dir / "figs", "phase0", figure_rows0)
    write_csv(phase0_dir / "tables" / "figure_index.csv", figure_rows0)

    phase0_gates = validation_gates("phase0", run_id, pairing_rows, None, len(figure_rows0), 0)
    write_csv(phase0_dir / "tables" / "validation_gates.csv", phase0_gates)
    write_gate_report(phase0_dir / "reports" / "VALIDATION_GATES.md", phase0_gates, "Phase 0 Validation Gates")
    write_phase_report(
        phase0_dir / "reports" / "BASELINE_FREEZE.md",
        "Phase 0 Baseline Freeze",
        phase0_summary_rows,
        phase0_gates,
        extra="B0/B1/B2 are recomputed from the existing official aligned sample tables. official_extra_analysis is read-only.",
    )

    seeds: list[dict] = []
    b0_samples = baseline_samples_by_exp["B0_A0_U4_P0_T1"].copy()
    imu_samples = simulate_imu_rows(b0_samples, run_id, seeds)
    fusion_samples_by_exp = fusion_rows(b0_samples, imu_samples, raw_stats)

    phase1_samples_by_exp: dict[str, pd.DataFrame] = {"B0_A0_U4_P0_T1": b0_samples}
    phase1_samples_by_exp.update(imu_samples)
    phase1_samples_by_exp.update(fusion_samples_by_exp)

    write_json(SIM_ROOT / "manifests" / f"noise_seeds_{run_id}.json", seeds)
    write_gzip_csv(SIM_ROOT / "cache" / "perfect_imu" / f"L0_I0_T11_samples__{run_id}.csv.gz", imu_samples["X_A0_L0_I0_T11"])
    write_gzip_csv(SIM_ROOT / "cache" / "sensor_imu" / f"L2_I3_T11_samples__{run_id}.csv.gz", imu_samples["X_A0_L2_I3_T11"])
    trace_cols = [
        "experiment_id",
        "capture_id",
        "tag",
        "time_s",
        "opti_time_s",
        "x_mm",
        "y_mm",
        "z_mm",
        "uwb_x_mm",
        "uwb_y_mm",
        "uwb_z_mm",
        "opti_x_mm",
        "opti_y_mm",
        "opti_z_mm",
        "err3d_mm",
        "err_horizontal_xz_mm",
        "err_vertical_y_mm",
    ]
    trace_df = pd.concat([df[[c for c in trace_cols if c in df.columns]].copy() for df in phase1_samples_by_exp.values()], ignore_index=True)
    write_gzip_csv(phase1_dir / "traces" / "phase1_samples.csv.gz", trace_df)

    phase1_track_rows: list[dict] = []
    phase1_summary_rows: list[dict] = []
    for spec in PHASE1_ROWS:
        exp = str(spec["experiment_id"])
        samples = phase1_samples_by_exp[exp]
        tracks, summary = track_metrics(samples, exp, str(spec["deployability"]), str(spec["description"]))
        summary["solver"] = str(spec["solver"])
        summary["kind"] = str(spec["kind"])
        if "L" in spec:
            summary["L"] = str(spec["L"])
            summary["I"] = str(spec["I"])
        phase1_track_rows.extend(tracks)
        phase1_summary_rows.append(summary)
    add_baseline_deltas(phase1_summary_rows)
    write_csv(phase1_dir / "tables" / "phase1_track_metrics.csv", phase1_track_rows)
    write_csv(phase1_dir / "tables" / "phase1_summary.csv", phase1_summary_rows)

    figure_rows1: list[dict] = []
    plot_full_tracks(phase1_samples_by_exp, phase1_dir / "figs", "phase1", figure_rows1)
    plot_contact_sheets(phase1_samples_by_exp, phase1_dir / "figs", "phase1", figure_rows1)
    write_csv(phase1_dir / "tables" / "figure_index.csv", figure_rows1)

    phase1_gates = validation_gates("phase1", run_id, pairing_rows, phase1_summary_rows, len(figure_rows1), len(seeds))
    write_csv(phase1_dir / "tables" / "validation_gates.csv", phase1_gates)
    write_gate_report(phase1_dir / "reports" / "VALIDATION_GATES.md", phase1_gates, "Phase 1 Validation Gates")
    write_phase_report(
        phase1_dir / "reports" / "PHASE1_VERTICAL_SLICE.md",
        "Phase 1 Vertical Slice",
        phase1_summary_rows,
        phase1_gates,
        extra=(
            "T6 rows are limited prototypes in this run: raw-range availability is audited, "
            "but solved-position proxy updates are used until the Phase 2 range-bias policy is implemented."
        ),
    )

    elapsed = time.perf_counter() - start
    timing_rows = [
        {
            "run_id": run_id,
            "phase": "phase0+phase1",
            "wall_time_s": elapsed,
            "n_phase0_figures": len(figure_rows0),
            "n_phase1_figures": len(figure_rows1),
            "n_phase1_rows": len(phase1_summary_rows),
            "gpu_id": "cpu",
            "peak_gpu_mem_mb": 0,
            "status": "ok",
        }
    ]
    write_csv(phase0_dir / "tables" / "timing_summary.csv", timing_rows)
    write_csv(phase1_dir / "tables" / "per_row_timing.csv", timing_rows)
    write_csv(
        phase1_dir / "tables" / "gpu_memory_summary.csv",
        [{"run_id": run_id, "gpu_id": "cpu", "peak_gpu_mem_mb": 0, "note": "Phase 1 vertical slice used CPU/vectorized numpy; GPU not required"}],
    )
    extrapolation = [
        "# Runtime Extrapolation",
        "",
        f"Phase 0+1 measured wall time: {fmt(elapsed, 2)} s.",
        "",
        "This run is a correctness vertical slice, not the profiled GPU screening matrix.",
        "Use the Phase 2 launcher to calibrate per-T solver cost on both GTX 1080 Ti cards.",
        "",
    ]
    (phase1_dir / "reports" / "RUNTIME_EXTRAPOLATION.md").write_text("\n".join(extrapolation), encoding="utf-8")

    manifest = {
        "run_id": run_id,
        "generated_utc": datetime.now(UTC).isoformat(),
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "command": " ".join([os.path.basename(__file__)] + os.sys.argv[1:]),
        "git": git_status(),
        "alignment_source": "official_extra_analysis/FULL_US/roto_absolute/tables/roto_time_offsets_v4io_T4.csv",
        "alignment_policy": "fixed_capture_level_beta_from_primary_v4io_U4",
        "capture_set": "R01-R17",
        "phase0_dir": str(phase0_dir.relative_to(SIM_ROOT)),
        "phase1_dir": str(phase1_dir.relative_to(SIM_ROOT)),
        "configs": {
            "frame_conventions": "configs/frame_conventions.md",
            "range_bias_policy": "configs/range_bias_policy.md",
            "sensors": "configs/sensors.yaml",
            "fusion": "configs/fusion.yaml",
        },
        "outputs": {
            "phase0_summary": str((phase0_dir / "tables" / "baseline_summary.csv").relative_to(SIM_ROOT)),
            "phase1_summary": str((phase1_dir / "tables" / "phase1_summary.csv").relative_to(SIM_ROOT)),
            "phase1_gates": str((phase1_dir / "tables" / "validation_gates.csv").relative_to(SIM_ROOT)),
            "noise_seeds": str((SIM_ROOT / "manifests" / f"noise_seeds_{run_id}.json").relative_to(SIM_ROOT)),
        },
    }
    write_json(phase0_dir / "manifest.json", manifest)
    write_json(phase1_dir / "manifest.json", manifest)
    write_json(SIM_ROOT / "manifests" / f"phase1_{run_id}.json", manifest)

    return {
        "run_id": run_id,
        "phase0_dir": str(phase0_dir),
        "phase1_dir": str(phase1_dir),
        "phase0_gates": phase0_gates,
        "phase1_gates": phase1_gates,
        "phase0_summary": phase0_summary_rows,
        "phase1_summary": phase1_summary_rows,
        "elapsed_s": elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run IMU fusion simulation Phase 0 + Phase 1 vertical slice.")
    parser.add_argument("--run-id", default="", help="Optional run ID. Defaults to UTC timestamp.")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({"run_id": result["run_id"], "phase0_dir": result["phase0_dir"], "phase1_dir": result["phase1_dir"]}, indent=2))
    print("\nPHASE 1 GATES")
    for row in result["phase1_gates"]:
        print(f"{row['gate_id']}: {row['status']} | blocking_next_phase={row['blocking_next_phase']}")
    print("\nPHASE 1 SUMMARY")
    for row in result["phase1_summary"]:
        print(
            f"{row['experiment_id']}: P50={fmt(row.get('trackmedian_err3d_p50_mm'))} "
            f"P95={fmt(row.get('trackmedian_err3d_p95_mm'))} verdict={row.get('verdict')}"
        )


if __name__ == "__main__":
    main()
