#!/usr/bin/env python3
"""Run Phase 2 stage1 broad screening inside an existing phase2_screening run."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import math
import multiprocessing as mp
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
ANALYSIS_ROOT = SIM_ROOT.parent
OFFICIAL_ROOT = ANALYSIS_ROOT.parent
EXTRA_ROOT = ANALYSIS_ROOT / "official_extra_analysis"
CAPTURES_ROOT = OFFICIAL_ROOT / "captures" / "erlangen_20260528_optitrack"
PHASE1_SCRIPT = SIM_ROOT / "scripts" / "run_phase0_phase1_vertical_slice.py"

ROTO_IDS = [f"R{i:02d}" for i in range(1, 18)]
TAGS = ["BS2DCE", "BSDC91"]
G_MM_S2 = 9806.65

L_IDS = ["L0", "L1", "L2", "L3", "L4", "L5", "L7", "L8"]
I_IDS = ["I0", "I1", "I3", "I4", "I7", "I8", "I1+I3+I7", "I1+I2+I3+I8"]
UP_IDS = [("U4", "P0"), ("U4", "P2")]
R_IDS = ["R2", "R4"]
POS_T_IDS = ["T2", "T3", "T5"]
RANGE_T_IDS = ["T6", "T8"]


L_PROPS = {
    "L0": {"bias_mg": 0.0, "noise_mg": 0.0, "rw_mg": 0.0, "vib_mg": 0.0, "extrinsic_mg": 0.0},
    "L1": {"bias_mg": 0.03, "noise_mg": 0.15, "rw_mg": 0.005, "vib_mg": 0.02, "extrinsic_mg": 0.0},
    "L2": {"bias_mg": 0.20, "noise_mg": 0.80, "rw_mg": 0.030, "vib_mg": 0.25, "extrinsic_mg": 0.05},
    "L3": {"bias_mg": 0.15, "noise_mg": 0.60, "rw_mg": 0.025, "vib_mg": 0.18, "extrinsic_mg": 0.04},
    "L4": {"bias_mg": 0.50, "noise_mg": 1.40, "rw_mg": 0.060, "vib_mg": 0.45, "extrinsic_mg": 0.12},
    "L5": {"bias_mg": 0.08, "noise_mg": 0.35, "rw_mg": 0.012, "vib_mg": 0.08, "extrinsic_mg": 0.03},
    "L7": {"bias_mg": 1.20, "noise_mg": 3.00, "rw_mg": 0.120, "vib_mg": 1.00, "extrinsic_mg": 0.35},
    "L8": {"bias_mg": 0.25, "noise_mg": 0.90, "rw_mg": 0.040, "vib_mg": 0.35, "extrinsic_mg": 0.65},
}


I_MODS = {
    "I0": {"bias": 2.2, "noise": 1.2, "rw": 1.5, "vib": 1.2, "process": 1.5},
    "I1": {"bias": 1.0, "noise": 0.55, "rw": 1.0, "vib": 0.75, "process": 0.9},
    "I3": {"bias": 0.40, "noise": 1.0, "rw": 0.85, "vib": 1.0, "process": 0.8},
    "I4": {"bias": 0.70, "noise": 0.80, "rw": 0.75, "vib": 0.8, "process": 0.75},
    "I7": {"bias": 0.90, "noise": 0.75, "rw": 0.95, "vib": 0.45, "process": 0.85},
    "I8": {"bias": 0.70, "noise": 0.70, "rw": 0.70, "vib": 0.65, "process": 0.70},
    "I1+I3+I7": {"bias": 0.28, "noise": 0.45, "rw": 0.65, "vib": 0.35, "process": 0.55},
    "I1+I2+I3+I8": {"bias": 0.22, "noise": 0.40, "rw": 0.55, "vib": 0.40, "process": 0.50},
}


T_PARAMS = {
    "T2": {"prior_sigma_base": 22.0, "measurement_sigma": 90.0, "deployability": "position_prior_screening"},
    "T3": {"prior_sigma_base": 70.0, "measurement_sigma": 90.0, "deployability": "loose_ekf_screening"},
    "T5": {"prior_sigma_base": 105.0, "measurement_sigma": 80.0, "deployability": "error_state_ekf_screening"},
    "T6": {"prior_sigma_base": 65.0, "range_sigma": 260.0, "deployability": "range_ekf_screening"},
    "T8": {"prior_sigma_base": 95.0, "range_sigma": 320.0, "deployability": "robust_range_ekf_screening"},
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


P1 = load_module(PHASE1_SCRIPT, "phase1_vertical_slice_for_stage1")

_POSITION_STREAMS: dict[tuple[str, str], pd.DataFrame] = {}
_POSITION_IMU_BY_LI: dict[tuple[str, str], pd.DataFrame] = {}
_RANGE_RAW_BY_TRACK: dict[tuple[str, str], pd.DataFrame] = {}
_RANGE_IMU_BY_LI: dict[tuple[str, str], pd.DataFrame] = {}
_RANGE_ANCHOR_XYZ: np.ndarray | None = None
_RANGE_ANCHOR_DELAY: np.ndarray | None = None
_RANGE_TAG_DELAY: float = 0.0
_RANGE_BIAS: np.ndarray | None = None
_RANGE_SIGMA: np.ndarray | None = None


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


def fmt(value: object, digits: int = 1) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(f):
        return "nan"
    return f"{f:.{digits}f}"


def stable_seed(*parts: object) -> int:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "little") & 0x7FFFFFFF


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


def markdown_table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return ""
    out = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        vals = []
        for col in columns:
            val = row.get(col, "")
            vals.append(fmt(val) if isinstance(val, float) else str(val))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)


def git_status() -> dict[str, object]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=OFFICIAL_ROOT.parent, text=True).strip()
    except Exception:
        commit = "unknown"
    try:
        status = subprocess.check_output(["git", "status", "--short"], cwd=OFFICIAL_ROOT.parent, text=True).strip().splitlines()
    except Exception:
        status = []
    return {"commit": commit, "dirty": bool(status), "status_short": status[:200]}


def latest_phase2_ready_run() -> str:
    root = SIM_ROOT / "runs" / "phase2_screening"
    candidates = []
    for p in root.iterdir():
        manifest = p / "manifest.json"
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("phase_status") == "ready_for_stage1_screening":
            candidates.append(p.name)
    if not candidates:
        raise RuntimeError("no phase2_screening run with phase_status=ready_for_stage1_screening")
    return sorted(candidates)[-1]


def load_b0_samples() -> pd.DataFrame:
    case = P1.BASELINES[0]
    df = P1.load_official_samples(case.sample_path)
    return P1.official_to_samples(df, case.experiment_id, case.deployability, case.description)


def robust_p2_stream(b0: pd.DataFrame) -> pd.DataFrame:
    out_chunks = []
    for (_capture_id, _tag), g0 in b0.groupby(["capture_id", "tag"], sort=True):
        g = g0.sort_values("time_s").copy()
        for axis in ["x", "y", "z"]:
            col = f"uwb_{axis}_mm"
            vals = g[col].to_numpy(float)
            med = pd.Series(vals).rolling(window=5, center=True, min_periods=1).median().to_numpy(float)
            resid = vals - med
            scale = 1.4826 * np.nanmedian(np.abs(resid - np.nanmedian(resid)))
            if not math.isfinite(scale) or scale < 1.0:
                scale = 80.0
            cleaned = np.where(np.abs(resid) > 3.0 * scale, med, vals)
            smooth = np.empty_like(cleaned)
            alpha = 0.72
            smooth[0] = cleaned[0]
            for i in range(1, len(cleaned)):
                smooth[i] = alpha * cleaned[i] + (1.0 - alpha) * smooth[i - 1]
            g[col] = smooth
        g["x_mm"] = g["uwb_x_mm"]
        g["y_mm"] = g["uwb_y_mm"]
        g["z_mm"] = g["uwb_z_mm"]
        P1.add_errors(g)
        out_chunks.append(g)
    return pd.concat(out_chunks, ignore_index=True)


def li_process_factor(l_id: str, i_id: str) -> float:
    prop = L_PROPS[l_id]
    mod = I_MODS[i_id]
    raw = (
        1.0
        + 2.0 * prop["bias_mg"] * mod["bias"]
        + 0.5 * prop["noise_mg"] * mod["noise"]
        + 10.0 * prop["rw_mg"] * mod["rw"]
        + 0.3 * prop["vib_mg"] * mod["vib"]
        + 1.0 * prop["extrinsic_mg"]
    )
    return max(0.25, raw * mod["process"])


def simulate_imu_for_li(b0: pd.DataFrame, run_id: str, l_id: str, i_id: str) -> pd.DataFrame:
    prop = L_PROPS[l_id]
    mod = I_MODS[i_id]
    chunks = []
    for (capture_id, tag), g0 in b0.groupby(["capture_id", "tag"], sort=True):
        g = g0.sort_values("time_s").copy()
        truth = g[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float)
        times = g["time_s"].to_numpy(float)
        drift = np.zeros_like(truth)
        if l_id != "L0":
            seed = stable_seed(run_id, "stage1", l_id, i_id, capture_id, tag)
            rng = np.random.default_rng(seed)
            vel = np.zeros(3, dtype=float)
            pos = np.zeros(3, dtype=float)
            bias_sigma = prop["bias_mg"] * mod["bias"] * G_MM_S2 / 1000.0
            noise_sigma = prop["noise_mg"] * mod["noise"] * G_MM_S2 / 1000.0
            rw_base = prop["rw_mg"] * mod["rw"] * G_MM_S2 / 1000.0
            vib_amp = prop["vib_mg"] * mod["vib"] * G_MM_S2 / 1000.0
            extrinsic = prop["extrinsic_mg"] * G_MM_S2 / 1000.0 * rng.normal(0.0, 1.0, size=3)
            bias = rng.normal(0.0, bias_sigma, size=3) + extrinsic
            phase = rng.uniform(0.0, 2.0 * math.pi, size=3)
            freq = rng.uniform(6.0, 15.0, size=3)
            for i in range(1, len(times)):
                dt = float(times[i] - times[i - 1])
                if not math.isfinite(dt) or dt <= 0:
                    dt = 1.0 / 15.0
                dt = min(max(dt, 1e-3), 0.25)
                bias = bias + rng.normal(0.0, rw_base * math.sqrt(dt), size=3)
                vib = vib_amp * np.sin(2.0 * math.pi * freq * times[i] + phase)
                acc_err = bias + rng.normal(0.0, noise_sigma, size=3) + vib
                pos = pos + vel * dt + 0.5 * acc_err * dt * dt
                vel = vel + acc_err * dt
                drift[i] = pos
        out = g.copy()
        out["experiment_id"] = f"X_A0_{l_id}_{i_id}_T11"
        out["deployability"] = "imu_only_diagnostic_oracle" if l_id == "L0" else "imu_only_diagnostic_screening"
        out["description"] = f"Phase 2 IMU-only drift diagnostic {l_id}/{i_id}"
        out["L"] = l_id
        out["I"] = i_id
        out["x_mm"] = truth[:, 0] + drift[:, 0]
        out["y_mm"] = truth[:, 1] + drift[:, 1]
        out["z_mm"] = truth[:, 2] + drift[:, 2]
        endpoint = drift[-1] if len(drift) else np.full(3, np.nan)
        endpoint_3d = float(np.linalg.norm(endpoint))
        duration = float(times[-1] - times[0]) if len(times) > 1 else float("nan")
        out["imu_only_endpoint_drift_3d_mm"] = endpoint_3d
        out["imu_only_endpoint_drift_xz_mm"] = float(math.sqrt(endpoint[0] * endpoint[0] + endpoint[2] * endpoint[2]))
        out["imu_only_endpoint_drift_y_mm"] = float(abs(endpoint[1]))
        out["imu_only_drift_rate_3d_mm_s"] = endpoint_3d / duration if math.isfinite(duration) and duration > 0 else float("nan")
        P1.add_errors(out)
        chunks.append(out)
    return pd.concat(chunks, ignore_index=True)


def interpolate_xyz(time_s: np.ndarray, xyz: np.ndarray, query_s: np.ndarray) -> np.ndarray:
    t = np.asarray(time_s, dtype=float)
    pts = np.asarray(xyz, dtype=float)
    q = np.asarray(query_s, dtype=float)
    out = np.full((q.size, 3), np.nan, dtype=float)
    good = np.isfinite(t) & np.isfinite(pts).all(axis=1)
    if int(np.sum(good)) < 2:
        return out
    order = np.argsort(t[good])
    tg = t[good][order]
    pg = pts[good][order]
    for axis in range(3):
        out[:, axis] = np.interp(q, tg, pg[:, axis], left=np.nan, right=np.nan)
    return out


def position_fusion_samples(
    stream: pd.DataFrame,
    prior: pd.DataFrame,
    experiment_id: str,
    deployability: str,
    description: str,
    prior_sigma: float,
    measurement_sigma: float,
) -> pd.DataFrame:
    prior_by_track = {k: g.sort_values("time_s") for k, g in prior.groupby(["capture_id", "tag"], sort=True)}
    chunks = []
    for (capture_id, tag), g0 in stream.groupby(["capture_id", "tag"], sort=True):
        g = g0.sort_values("time_s").copy()
        pg = prior_by_track[(capture_id, tag)]
        meas = g[["uwb_x_mm", "uwb_y_mm", "uwb_z_mm"]].to_numpy(float)
        pxyz = pg[["x_mm", "y_mm", "z_mm"]].to_numpy(float)
        fused, innovation, nis, prior_missing = P1.relative_motion_filter(
            g["time_s"].to_numpy(float), meas, pxyz, prior_sigma, measurement_sigma
        )
        out = g.copy()
        out["experiment_id"] = experiment_id
        out["deployability"] = deployability
        out["description"] = description
        out["x_mm"] = fused[:, 0]
        out["y_mm"] = fused[:, 1]
        out["z_mm"] = fused[:, 2]
        out["uwb_update_accept_rate"] = float(np.mean(np.isfinite(meas).all(axis=1)))
        out["uwb_innovation_nis"] = nis
        out["uwb_innovation_nis_median"] = pct(nis, 50)
        out["uwb_innovation_nis_p95"] = pct(nis, 95)
        out["prior_missing_samples"] = prior_missing
        out["filter_divergence_count"] = 0
        P1.add_errors(out)
        chunks.append(out)
    return pd.concat(chunks, ignore_index=True)


def load_a0_layout() -> tuple[np.ndarray, np.ndarray, float]:
    path = OFFICIAL_ROOT / "solver" / "outputs" / "v1_to_v4_io_field_check_US" / "v4-io" / "layout.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    anchors = sorted(data["anchors"], key=lambda row: int(row["id"]))
    xyz = np.asarray([[float(a["x_mm"]), float(a["y_mm"]), float(a["z_mm"])] for a in anchors], dtype=float)
    delays = np.asarray([float(a.get("d_anchor_mm", 0.0)) for a in anchors], dtype=float)
    return xyz, delays, float(data.get("tag_delay_mm", 0.0))


def load_range_policy(run_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    p = run_dir / "stage0_gate_fulfillment" / "tables" / "g3_range_bias_policy_R2.csv"
    df = pd.read_csv(p)
    bias = np.zeros(8, dtype=float)
    sigma = np.full(8, 300.0, dtype=float)
    for _, row in df.iterrows():
        aid = int(row["anchor_id"])
        bias[aid] = float(row["range_bias_mm"])
        sigma[aid] = max(80.0, float(row["range_sigma_mm"]))
    return bias, sigma


def load_raw_frames(b0: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    pairing = P1.build_pairing_manifest()
    raw_ready = [
        r
        for r in pairing
        if int(r.get("uwb_capture_count", 0)) == 1 and str(r.get("alignment_status")) == "ok"
    ]
    beta_by_capture = {str(r["capture_id"]): float(r["beta_s"]) for r in raw_ready}
    b0_by_track = {k: g.sort_values("opti_time_s") for k, g in b0.groupby(["capture_id", "tag"], sort=True)}
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for pair in raw_ready:
        cap_id = str(pair["capture_id"])
        cap_dir = OFFICIAL_ROOT / str(pair["uwb_capture_path"])
        tr = sorted(cap_dir.glob("tag_capture*/tr_all.csv"))[0]
        raw = pd.read_csv(tr, usecols=["host_elapsed_s", "sweep", "peer_name", "anchor_id", "range_mm", "quality_percent", "valid"])
        raw = raw[(raw["valid"].astype(float) > 0) & (raw["range_mm"].astype(float) > 0)].copy()
        raw["anchor_id"] = raw["anchor_id"].astype(int)
        raw = raw[raw["anchor_id"].between(0, 7)].copy()
        for tag in TAGS:
            g = raw[raw["peer_name"].astype(str) == tag].copy()
            if g.empty:
                continue
            opti_time = g["host_elapsed_s"].to_numpy(float) + beta_by_capture[cap_id]
            samples = b0_by_track[(cap_id, tag)]
            opti_xyz = interpolate_xyz(
                samples["opti_time_s"].to_numpy(float),
                samples[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float),
                opti_time,
            )
            uwb_xyz = interpolate_xyz(
                samples["opti_time_s"].to_numpy(float),
                samples[["uwb_x_mm", "uwb_y_mm", "uwb_z_mm"]].to_numpy(float),
                opti_time,
            )
            gg = g.copy()
            gg["capture_id"] = cap_id
            gg["tag"] = tag
            gg["time_s"] = gg["host_elapsed_s"].astype(float)
            gg["opti_time_s"] = opti_time
            gg["opti_x_mm"] = opti_xyz[:, 0]
            gg["opti_y_mm"] = opti_xyz[:, 1]
            gg["opti_z_mm"] = opti_xyz[:, 2]
            gg["uwb_x_mm"] = uwb_xyz[:, 0]
            gg["uwb_y_mm"] = uwb_xyz[:, 1]
            gg["uwb_z_mm"] = uwb_xyz[:, 2]
            out[(cap_id, tag)] = gg
    return out


def range_ekf_track(
    raw: pd.DataFrame,
    prior_track: pd.DataFrame,
    anchor_xyz: np.ndarray,
    anchor_delay: np.ndarray,
    tag_delay: float,
    range_bias: np.ndarray,
    range_sigma: np.ndarray,
    prior_sigma: float,
    robust: bool,
) -> pd.DataFrame:
    frames = []
    for (time_s, sweep), g in raw.groupby(["time_s", "sweep"], sort=True):
        frames.append((float(time_s), int(sweep), g))
    if not frames:
        return pd.DataFrame()
    raw_times = np.asarray([f[0] for f in frames], dtype=float)
    raw_opti_times = np.asarray([float(f[2]["opti_time_s"].iloc[0]) for f in frames], dtype=float)
    opti_xyz = np.asarray([[float(f[2]["opti_x_mm"].iloc[0]), float(f[2]["opti_y_mm"].iloc[0]), float(f[2]["opti_z_mm"].iloc[0])] for f in frames])
    uwb_xyz = np.asarray([[float(f[2]["uwb_x_mm"].iloc[0]), float(f[2]["uwb_y_mm"].iloc[0]), float(f[2]["uwb_z_mm"].iloc[0])] for f in frames])
    prior_xyz = interpolate_xyz(
        prior_track["opti_time_s"].to_numpy(float),
        prior_track[["x_mm", "y_mm", "z_mm"]].to_numpy(float),
        raw_opti_times,
    )
    x = uwb_xyz[0].copy() if np.isfinite(uwb_xyz[0]).all() else prior_xyz[0].copy()
    if not np.isfinite(x).all():
        x = np.nanmean(opti_xyz, axis=0)
    p = np.eye(3, dtype=float) * 180.0**2
    ident = np.eye(3)
    out = np.full_like(opti_xyz, np.nan, dtype=float)
    nis_vals = np.full(len(frames), np.nan, dtype=float)
    accept = np.zeros(len(frames), dtype=float)
    prev_prior = prior_xyz[0].copy() if np.isfinite(prior_xyz[0]).all() else None
    for idx, (_time_s, _sweep, g) in enumerate(frames):
        if idx > 0 and np.isfinite(prior_xyz[idx]).all() and prev_prior is not None:
            delta = prior_xyz[idx] - prev_prior
            q_scale = 1.0
        else:
            delta = np.zeros(3)
            q_scale = 12.0
        x_pred = x + delta
        p_pred = p + ident * (prior_sigma**2) * q_scale
        aid = g["anchor_id"].to_numpy(int)
        if aid.size >= 4:
            z = g["range_mm"].to_numpy(float) - range_bias[aid] - anchor_delay[aid] - tag_delay
            diff = x_pred[None, :] - anchor_xyz[aid]
            dist = np.linalg.norm(diff, axis=1).clip(min=1e-6)
            h = diff / dist[:, None]
            residual = z - dist
            sigma = range_sigma[aid].copy()
            if robust:
                gate = np.abs(residual) > 3.0 * sigma
                sigma[gate] *= 8.0
            r_inv = np.diag(1.0 / np.maximum(sigma, 40.0) ** 2)
            s = h @ p_pred @ h.T + np.linalg.pinv(r_inv)
            try:
                k = p_pred @ h.T @ np.linalg.pinv(s)
                x = x_pred + k @ residual
                p = (ident - k @ h) @ p_pred @ (ident - k @ h).T + k @ np.linalg.pinv(r_inv) @ k.T
                nis_vals[idx] = float(residual @ np.linalg.pinv(s) @ residual)
                accept[idx] = 1.0
            except np.linalg.LinAlgError:
                x = x_pred
                p = p_pred
        else:
            x = x_pred
            p = p_pred
        out[idx] = x
        if np.isfinite(prior_xyz[idx]).all():
            prev_prior = prior_xyz[idx].copy()
    result = pd.DataFrame(
        {
            "capture_id": str(raw["capture_id"].iloc[0]),
            "tag": str(raw["tag"].iloc[0]),
            "time_s": raw_times,
            "opti_time_s": raw_opti_times,
            "x_mm": out[:, 0],
            "y_mm": out[:, 1],
            "z_mm": out[:, 2],
            "uwb_x_mm": uwb_xyz[:, 0],
            "uwb_y_mm": uwb_xyz[:, 1],
            "uwb_z_mm": uwb_xyz[:, 2],
            "opti_x_mm": opti_xyz[:, 0],
            "opti_y_mm": opti_xyz[:, 1],
            "opti_z_mm": opti_xyz[:, 2],
            "uwb_update_accept_rate": float(np.mean(accept)),
            "uwb_innovation_nis_median": pct(nis_vals, 50),
            "uwb_innovation_nis_p95": pct(nis_vals, 95),
            "raw_range_ge4_ratio": float(np.mean(accept)),
            "raw_range_full8_ratio": float(np.mean([len(f[2]) >= 8 for f in frames])),
            "filter_divergence_count": 0,
        }
    )
    P1.add_errors(result)
    return result


def range_fusion_samples(
    raw_by_track: dict[tuple[str, str], pd.DataFrame],
    prior: pd.DataFrame,
    experiment_id: str,
    deployability: str,
    description: str,
    prior_sigma: float,
    range_sigma_scale: float,
    robust: bool,
    range_bias: np.ndarray,
    range_sigma: np.ndarray,
    anchor_xyz: np.ndarray,
    anchor_delay: np.ndarray,
    tag_delay: float,
) -> pd.DataFrame:
    prior_by_track = {k: g.sort_values("opti_time_s") for k, g in prior.groupby(["capture_id", "tag"], sort=True)}
    chunks = []
    sigma = range_sigma * range_sigma_scale
    for key, raw in raw_by_track.items():
        out = range_ekf_track(
            raw,
            prior_by_track[key],
            anchor_xyz,
            anchor_delay,
            tag_delay,
            range_bias,
            sigma,
            prior_sigma,
            robust,
        )
        out["experiment_id"] = experiment_id
        out["deployability"] = deployability
        out["description"] = description
        chunks.append(out)
    return pd.concat(chunks, ignore_index=True)


def summarize_experiment(samples: pd.DataFrame, experiment_id: str, deployability: str, description: str, labels: dict) -> tuple[list[dict], dict]:
    tracks, summary = P1.track_metrics(samples, experiment_id, deployability, description)
    summary.update(labels)
    return tracks, summary


def init_position_worker(
    streams: dict[tuple[str, str], pd.DataFrame],
    imu_by_li: dict[tuple[str, str], pd.DataFrame],
) -> None:
    global _POSITION_STREAMS, _POSITION_IMU_BY_LI
    _POSITION_STREAMS = streams
    _POSITION_IMU_BY_LI = imu_by_li


def init_range_worker(
    raw_by_track: dict[tuple[str, str], pd.DataFrame],
    imu_by_li: dict[tuple[str, str], pd.DataFrame],
    anchor_xyz: np.ndarray,
    anchor_delay: np.ndarray,
    tag_delay: float,
    range_bias: np.ndarray,
    range_sigma: np.ndarray,
) -> None:
    global _RANGE_RAW_BY_TRACK, _RANGE_IMU_BY_LI
    global _RANGE_ANCHOR_XYZ, _RANGE_ANCHOR_DELAY, _RANGE_TAG_DELAY, _RANGE_BIAS, _RANGE_SIGMA
    _RANGE_RAW_BY_TRACK = raw_by_track
    _RANGE_IMU_BY_LI = imu_by_li
    _RANGE_ANCHOR_XYZ = anchor_xyz
    _RANGE_ANCHOR_DELAY = anchor_delay
    _RANGE_TAG_DELAY = tag_delay
    _RANGE_BIAS = range_bias
    _RANGE_SIGMA = range_sigma


def position_worker(job: tuple[int, str, str, str, str, str]) -> dict:
    job_index, u_id, p_id, l_id, i_id, t_id = job
    t0 = time.perf_counter()
    stream = _POSITION_STREAMS[(u_id, p_id)]
    prior = _POSITION_IMU_BY_LI[(l_id, i_id)]
    params = T_PARAMS[t_id]
    process = li_process_factor(l_id, i_id)
    prior_sigma = float(params["prior_sigma_base"]) * process
    if l_id == "L0":
        prior_sigma = min(prior_sigma, 8.0 if t_id == "T2" else 35.0)
    exp = f"X_A0_{u_id}_{p_id}_{l_id}_{i_id}_{t_id}"
    samples = position_fusion_samples(
        stream,
        prior,
        exp,
        str(params["deployability"]),
        f"Phase 2 position-side {t_id} screening row with {u_id}/{p_id}/{l_id}/{i_id}.",
        prior_sigma,
        float(params["measurement_sigma"]),
    )
    tracks, summary = summarize_experiment(
        samples,
        exp,
        str(params["deployability"]),
        f"Phase 2 position-side {t_id} screening row.",
        {"A": "A0", "U": u_id, "P": p_id, "L": l_id, "I": i_id, "T": t_id, "kind": "position_fusion"},
    )
    return {
        "job_index": job_index,
        "tracks": tracks,
        "summary": summary,
        "timing": {"experiment_id": exp, "wall_time_s": time.perf_counter() - t0, "status": "ok"},
    }


def range_worker(job: tuple[int, str, str, str, str]) -> dict:
    job_index, r_id, l_id, i_id, t_id = job
    t0 = time.perf_counter()
    if _RANGE_ANCHOR_XYZ is None or _RANGE_ANCHOR_DELAY is None or _RANGE_BIAS is None or _RANGE_SIGMA is None:
        raise RuntimeError("range worker was not initialized")
    prior = _RANGE_IMU_BY_LI[(l_id, i_id)]
    params = T_PARAMS[t_id]
    process = li_process_factor(l_id, i_id)
    prior_sigma = float(params["prior_sigma_base"]) * process
    if l_id == "L0":
        prior_sigma = min(prior_sigma, 45.0)
    range_sigma_scale = 1.0 if r_id == "R2" else 1.35
    robust = t_id == "T8" or r_id == "R4"
    exp = f"X_A0_{r_id}_{l_id}_{i_id}_{t_id}"
    samples = range_fusion_samples(
        _RANGE_RAW_BY_TRACK,
        prior,
        exp,
        str(params["deployability"]),
        f"Phase 2 range-side {t_id} screening row with {r_id}/{l_id}/{i_id}.",
        prior_sigma,
        range_sigma_scale,
        robust,
        _RANGE_BIAS,
        _RANGE_SIGMA,
        _RANGE_ANCHOR_XYZ,
        _RANGE_ANCHOR_DELAY,
        _RANGE_TAG_DELAY,
    )
    tracks, summary = summarize_experiment(
        samples,
        exp,
        str(params["deployability"]),
        f"Phase 2 range-side {t_id} screening row.",
        {"A": "A0", "R": r_id, "L": l_id, "I": i_id, "T": t_id, "kind": "range_fusion"},
    )
    return {
        "job_index": job_index,
        "tracks": tracks,
        "summary": summary,
        "timing": {"experiment_id": exp, "wall_time_s": time.perf_counter() - t0, "status": "ok"},
    }


def collect_parallel(
    label: str,
    jobs: list[tuple],
    worker,
    max_workers: int,
    initializer,
    initargs: tuple,
) -> list[dict]:
    if not jobs:
        return []
    ctx = mp.get_context("fork")
    started = time.perf_counter()
    results: list[dict] = []
    print(f"[stage1] {label}: {len(jobs)} rows with {max_workers} workers", flush=True)
    with ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=ctx,
        initializer=initializer,
        initargs=initargs,
    ) as pool:
        futures = [pool.submit(worker, job) for job in jobs]
        for done_count, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            if done_count == 1 or done_count % 20 == 0 or done_count == len(jobs):
                elapsed = time.perf_counter() - started
                rate = done_count / elapsed if elapsed > 0 else float("nan")
                print(f"[stage1] {label}: {done_count}/{len(jobs)} rows done ({fmt(rate, 2)} rows/s)", flush=True)
    return sorted(results, key=lambda row: int(row["job_index"]))


def screening_score(row: dict) -> float:
    p50 = float(row.get("trackmedian_err3d_p50_mm", float("inf")))
    p95 = float(row.get("trackmedian_err3d_p95_mm", float("inf")))
    delta_r = float(row.get("legacy_deltaR_error_rms_mm", float("inf")))
    radius = float(row.get("trackmedian_radius_error_abs_mm", float("inf")))
    return p50 + 0.35 * p95 + 0.7 * delta_r + 0.5 * radius


def write_reports(run_dir: Path, summary_rows: list[dict], ranked_rows: list[dict], elapsed_s: float) -> None:
    cols = [
        "rank",
        "experiment_id",
        "screening_score",
        "trackmedian_err3d_p50_mm",
        "trackmedian_err3d_p95_mm",
        "legacy_deltaR_error_rms_mm",
        "trackmedian_radius_error_abs_mm",
        "verdict",
    ]
    report = [
        "# Phase 2 Screening",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "Phase status: `stage1_screening_complete`",
        f"Stage1 wall time: {fmt(elapsed_s, 2)} s",
        "",
        "## Top 50 By Screening Score",
        "",
        markdown_table(ranked_rows[:50], cols),
        "",
        "Stage2 should generate PNG/contact-sheet evidence for top rows, failure rows, and controls.",
        "",
    ]
    (run_dir / "reports" / "PHASE2_SCREENING.md").write_text("\n".join(report), encoding="utf-8")


def run(args: argparse.Namespace) -> dict:
    run_id = args.run_id or latest_phase2_ready_run()
    run_dir = SIM_ROOT / "runs" / "phase2_screening" / run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("phase_status") not in {"ready_for_stage1_screening", "stage1_screening_complete"}:
        raise RuntimeError(f"phase2 run is not ready for stage1: {manifest.get('phase_status')}")
    stage1 = run_dir / "stage1_screening"
    for d in [stage1 / "tables", stage1 / "logs", run_dir / "tables", run_dir / "reports"]:
        d.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    b0 = load_b0_samples()
    p0 = b0.copy()
    p2 = robust_p2_stream(b0)
    streams = {("U4", "P0"): p0, ("U4", "P2"): p2}
    raw_by_track = load_raw_frames(b0)
    anchor_xyz, anchor_delay, tag_delay = load_a0_layout()
    range_bias, range_sigma = load_range_policy(run_dir)
    cpu_count = os.cpu_count() or 2
    max_workers = int(getattr(args, "workers", 0) or min(10, max(2, cpu_count - 2)))
    max_workers = max(1, min(max_workers, cpu_count))
    print(f"[stage1] run_id={run_id} cpu_count={cpu_count} workers={max_workers}", flush=True)

    imu_by_li: dict[tuple[str, str], pd.DataFrame] = {}
    summary_rows: list[dict] = []
    track_rows: list[dict] = []
    timing_rows: list[dict] = []

    # Baseline control.
    t0 = time.perf_counter()
    tracks, summary = summarize_experiment(
        p0,
        "B0_A0_U4_P0_T1",
        "online_deployable_uwb_only",
        "Phase 2 control: frozen B0 UWB-only.",
        {"A": "A0", "U": "U4", "P": "P0", "T": "T1", "kind": "baseline"},
    )
    track_rows.extend(tracks)
    summary_rows.append(summary)
    timing_rows.append({"experiment_id": summary["experiment_id"], "wall_time_s": time.perf_counter() - t0, "status": "ok"})

    imu_total = len(L_IDS) * len(I_IDS)
    imu_done = 0
    print(f"[stage1] imu_only: {imu_total} rows", flush=True)
    for l_id in L_IDS:
        for i_id in I_IDS:
            t0 = time.perf_counter()
            imu = simulate_imu_for_li(b0, run_id, l_id, i_id)
            imu_by_li[(l_id, i_id)] = imu
            exp = f"X_A0_{l_id}_{i_id}_T11"
            tracks, summary = summarize_experiment(
                imu,
                exp,
                "imu_only_diagnostic_oracle" if l_id == "L0" else "imu_only_diagnostic_screening",
                f"Phase 2 IMU-only drift diagnostic {l_id}/{i_id}.",
                {"A": "A0", "L": l_id, "I": i_id, "T": "T11", "kind": "imu_only"},
            )
            track_rows.extend(tracks)
            summary_rows.append(summary)
            timing_rows.append({"experiment_id": exp, "wall_time_s": time.perf_counter() - t0, "status": "ok"})
            imu_done += 1
            if imu_done == 1 or imu_done % 8 == 0 or imu_done == imu_total:
                print(f"[stage1] imu_only: {imu_done}/{imu_total} rows done", flush=True)

    position_jobs: list[tuple[int, str, str, str, str, str]] = []
    for (u_id, p_id) in streams:
        for l_id in L_IDS:
            for i_id in I_IDS:
                for t_id in POS_T_IDS:
                    position_jobs.append((len(position_jobs), u_id, p_id, l_id, i_id, t_id))
    position_results = collect_parallel(
        "position_fusion",
        position_jobs,
        position_worker,
        max_workers,
        init_position_worker,
        (streams, imu_by_li),
    )
    for result in position_results:
        track_rows.extend(result["tracks"])
        summary_rows.append(result["summary"])
        timing_rows.append(result["timing"])

    range_jobs: list[tuple[int, str, str, str, str]] = []
    for r_id in R_IDS:
        for l_id in L_IDS:
            for i_id in I_IDS:
                for t_id in RANGE_T_IDS:
                    range_jobs.append((len(range_jobs), r_id, l_id, i_id, t_id))
    range_results = collect_parallel(
        "range_fusion",
        range_jobs,
        range_worker,
        max_workers,
        init_range_worker,
        (raw_by_track, imu_by_li, anchor_xyz, anchor_delay, tag_delay, range_bias, range_sigma),
    )
    for result in range_results:
        track_rows.extend(result["tracks"])
        summary_rows.append(result["summary"])
        timing_rows.append(result["timing"])

    P1.add_baseline_deltas(summary_rows)
    for row in summary_rows:
        row["screening_score"] = screening_score(row)

    ranked = sorted(
        [r for r in summary_rows if str(r.get("kind")) not in {"baseline", "imu_only"}],
        key=lambda r: (float(r.get("screening_score", float("inf"))), float(r.get("trackmedian_err3d_p95_mm", float("inf")))),
    )
    for idx, row in enumerate(ranked, start=1):
        row["rank"] = idx
    elapsed = time.perf_counter() - start
    write_csv(stage1 / "tables" / "phase2_stage1_summary.csv", summary_rows)
    write_csv(stage1 / "tables" / "phase2_stage1_track_metrics.csv", track_rows)
    write_csv(stage1 / "tables" / "phase2_stage1_timing.csv", timing_rows)
    write_csv(run_dir / "tables" / "phase2_summary.csv", summary_rows)
    write_csv(run_dir / "tables" / "phase2_ranked_top50.csv", ranked[:50])
    write_reports(run_dir, summary_rows, ranked, elapsed)

    manifest.update(
        {
            "phase_status": "stage1_screening_complete",
            "stage_completed": "stage1_screening",
            "stage1_elapsed_s": elapsed,
            "stage1_workers": max_workers,
            "stage1_row_count": len(summary_rows),
            "stage1_track_metric_rows": len(track_rows),
            "stage1_generated_utc": datetime.now(UTC).isoformat(),
            "git": git_status(),
            "outputs": {
                **manifest.get("outputs", {}),
                "phase2_summary": str((run_dir / "tables" / "phase2_summary.csv").relative_to(SIM_ROOT)),
                "phase2_ranked_top50": str((run_dir / "tables" / "phase2_ranked_top50.csv").relative_to(SIM_ROOT)),
            },
        }
    )
    write_json(run_dir / "manifest.json", manifest)
    write_json(SIM_ROOT / "manifests" / f"phase2_{run_id}.json", manifest)
    return {"run_id": run_id, "run_dir": str(run_dir), "elapsed_s": elapsed, "rows": len(summary_rows), "ranked": ranked[:10]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 2 stage1 screening inside an official phase2_screening run.")
    parser.add_argument("--run-id", default="", help="Existing phase2_screening run ID. Defaults to latest ready run.")
    parser.add_argument("--workers", type=int, default=0, help="CPU worker processes. Default: min(10, cpu_count - 2).")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({"run_id": result["run_id"], "run_dir": result["run_dir"], "rows": result["rows"], "elapsed_s": result["elapsed_s"]}, indent=2))
    print("\nTOP 10")
    for row in result["ranked"]:
        print(
            f"#{row['rank']} {row['experiment_id']} score={fmt(row['screening_score'])} "
            f"P50={fmt(row.get('trackmedian_err3d_p50_mm'))} P95={fmt(row.get('trackmedian_err3d_p95_mm'))} "
            f"dR={fmt(row.get('legacy_deltaR_error_rms_mm'))} verdict={row.get('verdict')}"
        )


if __name__ == "__main__":
    main()
