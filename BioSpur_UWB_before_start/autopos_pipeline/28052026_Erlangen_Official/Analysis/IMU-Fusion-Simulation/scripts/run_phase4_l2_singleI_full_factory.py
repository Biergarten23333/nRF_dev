#!/usr/bin/env python3
"""Run the Phase 4 single-I factory matrix for one selected IMU sensor.

This is the single-sensor/single-I launcher used for L2/JY61P/MPU6050-like
screening and follow-up L16/L20 replacement runs. It creates the declared
5292-row single-I/seed manifest and evaluates every row that has source
artifacts and a current implementation. Missing-source rows are recorded
explicitly instead of being silently pruned.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import multiprocessing as mp
import os
import platform
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
ANALYSIS_ROOT = SIM_ROOT.parent
OFFICIAL_ROOT = ANALYSIS_ROOT.parent
EXTRA_ROOT = ANALYSIS_ROOT / "official_extra_analysis"
STAGE1_SCRIPT = SIM_ROOT / "scripts" / "run_phase2_stage1_screening.py"
GPU_PILOT_SCRIPT = SIM_ROOT / "scripts" / "run_phase4_gpu_pilot.py"
SENSORS_YAML = SIM_ROOT / "configs" / "sensors.yaml"

A_CASES = {
    "A0": {
        "name": "AutoPos v4-io rigid no-scale",
        "path": EXTRA_ROOT / "FULL_US" / "roto_absolute" / "tables" / "roto_abs_samples_v4io_T4.csv",
        "deployability": "online_deployable_uwb_only",
    },
    "A1": {
        "name": "one-baseline scale correction",
        "path": EXTRA_ROOT / "FULL_AutoPos_one_baseline_scale_correction_US" / "roto_absolute" / "tables" / "roto_abs_samples_v4io_T4.csv",
        "deployability": "diagnostic_layout_control",
    },
    "A2": {
        "name": "Vicon/OptiTrack truth anchors + delaycal",
        "path": EXTRA_ROOT / "FULL_AutoPos_align_to_Vicon_US" / "roto_absolute" / "tables" / "roto_abs_samples_v4io_T4.csv",
        "deployability": "oracle_layout_control",
    },
    "A3": {
        "name": "full similarity scale-to-Vicon + delaycal",
        "path": EXTRA_ROOT / "FULL_AutoPos_scale_to_vicon_US" / "roto_absolute" / "tables" / "roto_abs_samples_v4io_T4.csv",
        "deployability": "diagnostic_scale_control",
    },
}

A_IDS = list(A_CASES)
U_IDS = [f"U{i}" for i in range(1, 5)]
CORRECTED_IMU_U_IDS = ["U4"]
P_IDS = [f"P{i}" for i in range(0, 6)]
R_IDS = [f"R{i}" for i in range(0, 5)]
I_IDS = [f"I{i}" for i in range(0, 9)]
L_IDS = ["L2"]
POS_T_IDS = [f"T{i}" for i in range(1, 6)]
RAW_T_IDS = [f"T{i}" for i in range(6, 11)]
IMU_T_IDS = ["T11", "T12"]

POSITION_T_PARAMS = {
    "T2": {"prior_sigma_base": 22.0, "measurement_sigma": 90.0, "deployability": "position_prior_screening"},
    "T3": {"prior_sigma_base": 70.0, "measurement_sigma": 90.0, "deployability": "loose_ekf_screening"},
    "T4": {"prior_sigma_base": 55.0, "measurement_sigma": 85.0, "deployability": "loose_ukf_screening"},
    "T5": {"prior_sigma_base": 105.0, "measurement_sigma": 80.0, "deployability": "error_state_ekf_screening"},
}

RAW_T_PARAMS = {
    "T6": {"prior_sigma_base": 65.0, "deployability": "range_ekf_screening", "smooth_alpha": 1.0},
    "T7": {"prior_sigma_base": 55.0, "deployability": "range_ukf_screening", "smooth_alpha": 0.92},
    "T8": {"prior_sigma_base": 95.0, "deployability": "robust_range_ekf_screening", "smooth_alpha": 1.0},
    "T9": {"prior_sigma_base": 42.0, "deployability": "session_window_factor_graph_proxy", "smooth_alpha": 0.72},
    "T10": {"prior_sigma_base": 30.0, "deployability": "full_session_batch_proxy", "smooth_alpha": 0.55},
}

EXTRA_I_MODS = {
    "I2": {"bias": 1.05, "noise": 0.75, "rw": 1.0, "vib": 0.35, "process": 0.85},
    "I5": {"bias": 0.55, "noise": 0.75, "rw": 0.55, "vib": 0.75, "process": 0.60},
    "I6": {"bias": 0.65, "noise": 0.85, "rw": 0.65, "vib": 0.80, "process": 0.65},
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


S1 = load_module(STAGE1_SCRIPT, "phase2_stage1_for_phase4_l2_full")
P1 = S1.P1
G = load_module(GPU_PILOT_SCRIPT, "phase4_gpu_backend_for_phase4_full")

_STREAMS: dict[tuple[str, str, str], pd.DataFrame] = {}
_IMU: dict[tuple[str, str, str], pd.DataFrame] = {}
_RAW_BY_TRACK: dict[tuple[str, str], pd.DataFrame] = {}
_ANCHOR_XYZ: np.ndarray | None = None
_ANCHOR_DELAY: np.ndarray | None = None
_TAG_DELAY = 0.0
_RANGE_BIAS: np.ndarray | None = None
_RANGE_SIGMA: np.ndarray | None = None
_L_PROPS: dict[str, dict] = {}
_SEED_ID = "S00"
_SENSOR_ID = "L2"


def fmt(value: object, digits: int = 1) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(f):
        return "nan"
    return f"{f:.{digits}f}"


def rms(values: object) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean(arr * arr)))


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
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


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


def torch_cuda_info() -> dict:
    try:
        import torch
    except Exception as exc:
        return {"torch_available": False, "error": str(exc)}
    out = {
        "torch_available": True,
        "torch_version": getattr(torch, "__version__", ""),
        "cuda_available": bool(torch.cuda.is_available()),
        "device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
        "devices": [],
    }
    if torch.cuda.is_available():
        for idx in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(idx)
            out["devices"].append({"index": idx, "name": props.name, "memory_total_mb": int(props.total_memory // (1024 * 1024))})
    return out


def install_sensor_props(sensor_id: str) -> tuple[dict[str, dict], dict[str, dict]]:
    raw = yaml.safe_load(SENSORS_YAML.read_text(encoding="utf-8"))
    if sensor_id not in raw:
        raise KeyError(f"{sensor_id} not found in {SENSORS_YAML}")
    row = raw[sensor_id]
    props = {
        sensor_id: {
            "bias_mg": float(row["residual_accel_bias_mg"]),
            "noise_mg": float(row["accel_noise_mg"]),
            "rw_mg": float(row["accel_bias_random_walk_mg_sqrt_s"]),
            "vib_mg": float(row["vibration_sensitivity_mg"]),
            "extrinsic_mg": float(row["extrinsic_mg"]),
            "gyro_bias_dps": float(row["residual_gyro_bias_dps"]),
            "gyro_noise_dps": float(row["gyro_noise_dps"]),
            "gyro_rw_dps_sqrt_s": float(row["gyro_bias_random_walk_dps_sqrt_s"]),
        }
    }
    S1.L_PROPS = props
    S1.I_MODS.update(EXTRA_I_MODS)
    return props, {sensor_id: row}


def p_filter(stream: pd.DataFrame, p_id: str) -> pd.DataFrame:
    out_chunks: list[pd.DataFrame] = []
    for (_capture_id, _tag), g0 in stream.groupby(["capture_id", "tag"], sort=True):
        g = g0.sort_values("time_s").copy()
        for axis in ["x", "y", "z"]:
            col = f"uwb_{axis}_mm"
            vals = g[col].to_numpy(float)
            if p_id == "P0":
                filtered = vals
            elif p_id == "P1":
                filtered = pd.Series(vals).rolling(window=5, center=True, min_periods=1).median().to_numpy(float)
            elif p_id == "P2":
                median = pd.Series(vals).rolling(window=5, center=True, min_periods=1).median().to_numpy(float)
                resid = vals - median
                scale = 1.4826 * np.nanmedian(np.abs(resid - np.nanmedian(resid)))
                if not math.isfinite(scale) or scale < 1.0:
                    scale = 80.0
                cleaned = np.where(np.abs(resid) > 3.0 * scale, median, vals)
                filtered = causal_smooth(cleaned, 0.72)
            elif p_id == "P3":
                median = pd.Series(vals).rolling(window=7, center=True, min_periods=1).median().to_numpy(float)
                resid = vals - median
                scale = 1.4826 * np.nanmedian(np.abs(resid - np.nanmedian(resid)))
                if not math.isfinite(scale) or scale < 1.0:
                    scale = 90.0
                filtered = np.where(np.abs(resid) > 2.5 * scale, median, vals)
            elif p_id == "P4":
                filtered = pd.Series(vals).rolling(window=9, center=True, min_periods=1).mean().to_numpy(float)
            elif p_id == "P5":
                filtered = pd.Series(vals).rolling(window=15, center=True, min_periods=1).mean().to_numpy(float)
                filtered = pd.Series(filtered).rolling(window=15, center=True, min_periods=1).median().to_numpy(float)
            else:
                raise ValueError(p_id)
            g[col] = filtered
        g["x_mm"] = g["uwb_x_mm"]
        g["y_mm"] = g["uwb_y_mm"]
        g["z_mm"] = g["uwb_z_mm"]
        P1.add_errors(g)
        out_chunks.append(g)
    return pd.concat(out_chunks, ignore_index=True)


def causal_smooth(vals: np.ndarray, alpha: float) -> np.ndarray:
    arr = np.asarray(vals, dtype=float)
    if arr.size == 0:
        return arr
    out = np.empty_like(arr)
    out[0] = arr[0]
    for idx in range(1, arr.size):
        out[idx] = alpha * arr[idx] + (1.0 - alpha) * out[idx - 1]
    return out


def session_smooth_samples(samples: pd.DataFrame, alpha: float) -> pd.DataFrame:
    if alpha >= 0.999:
        return samples
    chunks: list[pd.DataFrame] = []
    for (_capture_id, _tag), g0 in samples.groupby(["capture_id", "tag"], sort=True):
        g = g0.sort_values("time_s").copy()
        for axis in ["x", "y", "z"]:
            vals = g[f"{axis}_mm"].to_numpy(float)
            fwd = causal_smooth(vals, alpha)
            bwd = causal_smooth(vals[::-1], alpha)[::-1]
            smooth = 0.5 * (fwd + bwd)
            g[f"{axis}_mm"] = smooth
        P1.add_errors(g)
        chunks.append(g)
    return pd.concat(chunks, ignore_index=True)


def load_streams() -> dict[tuple[str, str, str], pd.DataFrame]:
    streams: dict[tuple[str, str, str], pd.DataFrame] = {}
    for a_id, case in A_CASES.items():
        df = P1.load_official_samples(case["path"])
        base = P1.official_to_samples(df, f"X_{a_id}_U4_P0_T1", str(case["deployability"]), f"{a_id} {case['name']} U4/P0/T1")
        for p_id in P_IDS:
            streams[(a_id, "U4", p_id)] = p_filter(base, p_id)
    return streams


def build_full_manifest() -> tuple[list[dict], list[dict], list[tuple], list[tuple], list[tuple]]:
    manifest: list[dict] = []
    exclusions: list[dict] = []
    pos_jobs: list[tuple] = []
    raw_jobs: list[tuple] = []
    imu_jobs: list[tuple] = []

    for a_id in A_IDS:
        for u_id in U_IDS:
            for p_id in P_IDS:
                for l_id in L_IDS:
                    for i_id in I_IDS:
                        for t_id in POS_T_IDS:
                            exp = f"X_{a_id}_{u_id}_{p_id}_{l_id}_{i_id}_{t_id}"
                            status = "runnable"
                            reason = ""
                            if u_id != "U4":
                                status = "excluded_source_missing"
                                reason = "only U4 solved-position sample stream exists on disk"
                            row = {"experiment_id": exp, "branch": "position", "A": a_id, "U": u_id, "P": p_id, "L": l_id, "I": i_id, "T": t_id, "seed_id": _SEED_ID, "status": status, "reason": reason}
                            manifest.append(row)
                            if status == "runnable":
                                pos_jobs.append((len(pos_jobs), a_id, u_id, p_id, l_id, i_id, t_id))
                            else:
                                exclusions.append(row)

    for a_id in A_IDS:
        for r_id in R_IDS:
            for l_id in L_IDS:
                for i_id in I_IDS:
                    for t_id in RAW_T_IDS:
                        exp = f"X_{a_id}_{r_id}_{l_id}_{i_id}_{t_id}"
                        status = "runnable"
                        reason = ""
                        if a_id != "A0":
                            status = "excluded_source_missing"
                            reason = "raw range path and anchor layout currently wired for A0 only"
                        row = {"experiment_id": exp, "branch": "raw_range", "A": a_id, "R": r_id, "L": l_id, "I": i_id, "T": t_id, "seed_id": _SEED_ID, "status": status, "reason": reason}
                        manifest.append(row)
                        if status == "runnable":
                            raw_jobs.append((len(raw_jobs), a_id, r_id, l_id, i_id, t_id))
                        else:
                            exclusions.append(row)

    for a_id in A_IDS:
        for l_id in L_IDS:
            for i_id in I_IDS:
                for t_id in IMU_T_IDS:
                    exp = f"X_{a_id}_{l_id}_{i_id}_{t_id}"
                    row = {"experiment_id": exp, "branch": "imu_only", "A": a_id, "L": l_id, "I": i_id, "T": t_id, "seed_id": _SEED_ID, "status": "runnable", "reason": ""}
                    manifest.append(row)
                    imu_jobs.append((len(imu_jobs), a_id, l_id, i_id, t_id))

    return manifest, exclusions, pos_jobs, raw_jobs, imu_jobs


def summarize(samples: pd.DataFrame, exp: str, deployability: str, description: str, labels: dict) -> tuple[list[dict], dict]:
    tracks, summary = S1.summarize_experiment(samples, exp, deployability, description, labels)
    for row in tracks:
        row["seed_id"] = _SEED_ID
    summary["seed_id"] = _SEED_ID
    summary["experiment_uid"] = f"{exp}__{_SEED_ID}"
    return tracks, summary


def init_imu_worker(streams, l_props):
    global _STREAMS, _L_PROPS
    _STREAMS = streams
    _L_PROPS = l_props
    S1.L_PROPS = l_props
    S1.I_MODS.update(EXTRA_I_MODS)


def init_position_worker(streams, imu, l_props):
    global _STREAMS, _IMU, _L_PROPS
    _STREAMS = streams
    _IMU = imu
    _L_PROPS = l_props
    S1.L_PROPS = l_props
    S1.I_MODS.update(EXTRA_I_MODS)


def init_raw_worker(raw_by_track, imu, anchor_xyz, anchor_delay, tag_delay, range_bias, range_sigma, l_props):
    global _RAW_BY_TRACK, _IMU, _ANCHOR_XYZ, _ANCHOR_DELAY, _TAG_DELAY, _RANGE_BIAS, _RANGE_SIGMA, _L_PROPS
    _RAW_BY_TRACK = raw_by_track
    _IMU = imu
    _ANCHOR_XYZ = anchor_xyz
    _ANCHOR_DELAY = anchor_delay
    _TAG_DELAY = tag_delay
    _RANGE_BIAS = range_bias
    _RANGE_SIGMA = range_sigma
    _L_PROPS = l_props
    S1.L_PROPS = l_props
    S1.I_MODS.update(EXTRA_I_MODS)


def imu_worker(job: tuple[int, str, str, str, str]) -> dict:
    job_index, a_id, l_id, i_id, t_id = job
    t0 = time.perf_counter()
    base = _STREAMS[(a_id, "U4", "P0")]
    imu = S1.simulate_imu_for_li(base, f"phase4_{l_id.lower()}_full_{_SEED_ID}_{a_id}", l_id, i_id)
    if t_id == "T12":
        imu = session_smooth_samples(imu, 0.82)
    exp = f"X_{a_id}_{l_id}_{i_id}_{t_id}"
    imu["experiment_id"] = exp
    imu["deployability"] = "imu_only_diagnostic_screening"
    imu["description"] = f"Phase4 {l_id} single-I {t_id} IMU-only diagnostic."
    tracks, summary = summarize(imu, exp, "imu_only_diagnostic_screening", f"Phase4 {l_id} single-I {t_id}.", {"A": a_id, "L": l_id, "I": i_id, "T": t_id, "kind": "imu_only"})
    return {"job_index": job_index, "key": (a_id, i_id, t_id), "samples": imu, "tracks": tracks, "summary": summary, "timing": {"experiment_id": exp, "seed_id": _SEED_ID, "wall_time_s": time.perf_counter() - t0, "status": "ok"}}


def position_worker(job: tuple[int, str, str, str, str, str, str]) -> dict:
    job_index, a_id, u_id, p_id, l_id, i_id, t_id = job
    t0 = time.perf_counter()
    stream = _STREAMS[(a_id, u_id, p_id)]
    exp = f"X_{a_id}_{u_id}_{p_id}_{l_id}_{i_id}_{t_id}"
    if t_id == "T1":
        samples = stream.copy()
        samples["experiment_id"] = exp
        samples["deployability"] = "uwb_only_control_repeated_for_matrix"
        samples["description"] = "Phase4 repeated UWB-only T1 control."
        deployability = "uwb_only_control_repeated_for_matrix"
    else:
        prior = _IMU[(a_id, i_id, "T11")]
        params = POSITION_T_PARAMS[t_id]
        process = S1.li_process_factor(l_id, i_id)
        samples = S1.position_fusion_samples(
            stream,
            prior,
            exp,
            str(params["deployability"]),
            f"Phase4 {l_id} single-I position-side {t_id}.",
            float(params["prior_sigma_base"]) * process,
            float(params["measurement_sigma"]),
        )
        deployability = str(params["deployability"])
    tracks, summary = summarize(samples, exp, deployability, f"Phase4 {l_id} single-I position row.", {"A": a_id, "U": u_id, "P": p_id, "L": l_id, "I": i_id, "T": t_id, "kind": "position_fusion" if t_id != "T1" else "uwb_only"})
    return {"job_index": job_index, "tracks": tracks, "summary": summary, "timing": {"experiment_id": exp, "seed_id": _SEED_ID, "wall_time_s": time.perf_counter() - t0, "status": "ok"}}


def raw_policy(r_id: str) -> tuple[np.ndarray, np.ndarray, float, bool]:
    if _RANGE_BIAS is None or _RANGE_SIGMA is None:
        raise RuntimeError("raw worker missing range policy")
    if r_id == "R0":
        return np.zeros_like(_RANGE_BIAS), np.full_like(_RANGE_SIGMA, 320.0), 1.25, False
    if r_id == "R1":
        return np.zeros_like(_RANGE_BIAS), np.maximum(_RANGE_SIGMA, 260.0), 1.10, False
    if r_id == "R2":
        return _RANGE_BIAS, _RANGE_SIGMA, 1.00, False
    if r_id == "R3":
        return _RANGE_BIAS, _RANGE_SIGMA, 1.05, True
    if r_id == "R4":
        return _RANGE_BIAS, _RANGE_SIGMA, 1.35, True
    raise ValueError(r_id)


def raw_policy_from_arrays(r_id: str, range_bias: np.ndarray, range_sigma: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, bool]:
    if r_id == "R0":
        return np.zeros_like(range_bias), np.full_like(range_sigma, 320.0), 1.25, False
    if r_id == "R1":
        return np.zeros_like(range_bias), np.maximum(range_sigma, 260.0), 1.10, False
    if r_id == "R2":
        return range_bias, range_sigma, 1.00, False
    if r_id == "R3":
        return range_bias, range_sigma, 1.05, True
    if r_id == "R4":
        return range_bias, range_sigma, 1.35, True
    raise ValueError(r_id)


def raw_worker(job: tuple[int, str, str, str, str, str]) -> dict:
    job_index, a_id, r_id, l_id, i_id, t_id = job
    t0 = time.perf_counter()
    if _ANCHOR_XYZ is None or _ANCHOR_DELAY is None:
        raise RuntimeError("raw worker missing anchor layout")
    prior = _IMU[(a_id, i_id, "T11")]
    params = RAW_T_PARAMS[t_id]
    process = S1.li_process_factor(l_id, i_id)
    bias, sigma, scale, r_robust = raw_policy(r_id)
    exp = f"X_{a_id}_{r_id}_{l_id}_{i_id}_{t_id}"
    samples = S1.range_fusion_samples(
        _RAW_BY_TRACK,
        prior,
        exp,
        str(params["deployability"]),
        f"Phase4 {l_id} single-I raw-range {t_id}/{r_id}.",
        float(params["prior_sigma_base"]) * process,
        scale,
        bool(r_robust or t_id in {"T8", "T9", "T10"}),
        bias,
        sigma,
        _ANCHOR_XYZ,
        _ANCHOR_DELAY,
        _TAG_DELAY,
    )
    samples = session_smooth_samples(samples, float(params.get("smooth_alpha", 1.0)))
    samples["experiment_id"] = exp
    samples["deployability"] = str(params["deployability"])
    samples["description"] = f"Phase4 {l_id} single-I raw-range {t_id}/{r_id}."
    tracks, summary = summarize(samples, exp, str(params["deployability"]), f"Phase4 {l_id} single-I raw-range row.", {"A": a_id, "R": r_id, "L": l_id, "I": i_id, "T": t_id, "kind": "range_fusion"})
    return {"job_index": job_index, "tracks": tracks, "summary": summary, "timing": {"experiment_id": exp, "seed_id": _SEED_ID, "wall_time_s": time.perf_counter() - t0, "status": "ok"}}


def gpu_range_samples(tensors: dict[str, object], gpu: dict[str, object], experiment_id: str, deployability: str, description: str) -> pd.DataFrame:
    xyz = np.asarray(gpu["xyz"], dtype=float)
    nis = np.asarray(gpu["nis"], dtype=float)
    accept = np.asarray(gpu["accept"], dtype=float)
    frame_mask = np.asarray(tensors["frame_mask"], dtype=bool)
    raw_time = np.asarray(tensors["raw_time"], dtype=float)
    opti_time = np.asarray(tensors["opti_time"], dtype=float)
    opti_xyz = np.asarray(tensors["opti_xyz"], dtype=float)
    uwb_xyz = np.asarray(tensors["uwb_xyz"], dtype=float)
    range_mask = np.asarray(tensors["range_mask"], dtype=bool)
    rows: list[pd.DataFrame] = []
    for bidx, key in enumerate(tensors["keys"]):
        valid = frame_mask[bidx]
        if not np.any(valid):
            continue
        n = int(np.sum(valid))
        acc = accept[bidx, valid]
        track_nis = nis[bidx, valid]
        full8 = np.sum(range_mask[bidx, valid, :], axis=1) >= 8
        df = pd.DataFrame(
            {
                "capture_id": str(key[0]),
                "tag": str(key[1]),
                "time_s": raw_time[bidx, valid],
                "opti_time_s": opti_time[bidx, valid],
                "x_mm": xyz[bidx, valid, 0],
                "y_mm": xyz[bidx, valid, 1],
                "z_mm": xyz[bidx, valid, 2],
                "uwb_x_mm": uwb_xyz[bidx, valid, 0],
                "uwb_y_mm": uwb_xyz[bidx, valid, 1],
                "uwb_z_mm": uwb_xyz[bidx, valid, 2],
                "opti_x_mm": opti_xyz[bidx, valid, 0],
                "opti_y_mm": opti_xyz[bidx, valid, 1],
                "opti_z_mm": opti_xyz[bidx, valid, 2],
                "uwb_update_accept_rate": float(np.mean(acc)) if n else float("nan"),
                "uwb_innovation_nis_median": S1.pct(track_nis, 50),
                "uwb_innovation_nis_p95": S1.pct(track_nis, 95),
                "raw_range_ge4_ratio": float(np.mean(acc)) if n else float("nan"),
                "raw_range_full8_ratio": float(np.mean(full8)) if n else float("nan"),
                "filter_divergence_count": 0,
            }
        )
        P1.add_errors(df)
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    out["experiment_id"] = experiment_id
    out["deployability"] = deployability
    out["description"] = description
    return out


def collect_raw_gpu(
    jobs: list[tuple],
    raw_by_track: dict[tuple[str, str], pd.DataFrame],
    imu_samples: dict[tuple[str, str, str], pd.DataFrame],
    anchor_xyz: np.ndarray,
    anchor_delay: np.ndarray,
    tag_delay: float,
    range_bias: np.ndarray,
    range_sigma: np.ndarray,
    device: str,
    gpu_workers: int,
) -> list[dict]:
    if not jobs:
        return []
    import torch

    if not torch.cuda.is_available() and device.startswith("cuda"):
        raise RuntimeError("CUDA raw backend requested, but torch.cuda.is_available() is false")
    started = time.perf_counter()
    gpu_workers = max(1, min(int(gpu_workers), len(jobs), 16))
    print(f"[phase4-full] raw_range_branch_gpu: {len(jobs)} rows on {device} with {gpu_workers} feeders", flush=True)
    tensor_cache: dict[tuple[str, str], dict[str, object]] = {}
    tensor_jobs = []
    seen_tensor_keys: set[tuple[str, str]] = set()
    for job in jobs:
        _, a_id, _r_id, _l_id, i_id, _t_id = job
        cache_key = (a_id, i_id)
        if cache_key not in seen_tensor_keys:
            seen_tensor_keys.add(cache_key)
            tensor_jobs.append((cache_key, a_id, i_id))
    tensor_started = time.perf_counter()
    for idx, (cache_key, a_id, i_id) in enumerate(tensor_jobs, start=1):
        t0 = time.perf_counter()
        tensor_cache[cache_key] = G.build_track_tensors(raw_by_track, imu_samples[(a_id, i_id, "T11")], 0, 0)
        print(
            f"[phase4-full] raw_range_branch_gpu: tensor_build {idx}/{len(tensor_jobs)} {a_id}/{i_id} "
            f"done ({fmt(time.perf_counter() - t0, 2)} s, total {fmt(time.perf_counter() - tensor_started, 2)} s)",
            flush=True,
        )

    def run_one(job: tuple) -> dict:
        job_index, a_id, r_id, l_id, i_id, t_id = job
        t0 = time.perf_counter()
        params = RAW_T_PARAMS[t_id]
        process = S1.li_process_factor(l_id, i_id)
        prior_sigma = float(params["prior_sigma_base"]) * process
        bias, sigma_base, scale, r_robust = raw_policy_from_arrays(r_id, range_bias, range_sigma)
        robust = bool(r_robust or t_id in {"T8", "T9", "T10"})
        exp = f"X_{a_id}_{r_id}_{l_id}_{i_id}_{t_id}"
        cache_key = (a_id, i_id)
        tensors = tensor_cache[cache_key]
        gpu = G.torch_range_ekf(
            tensors,
            anchor_xyz,
            anchor_delay,
            tag_delay,
            bias,
            sigma_base,
            prior_sigma,
            scale,
            robust,
            device,
            "float32",
        )
        samples = gpu_range_samples(
            tensors,
            gpu,
            exp,
            str(params["deployability"]),
            f"Phase4 {l_id} single-I raw-range {t_id}/{r_id}.",
        )
        samples = session_smooth_samples(samples, float(params.get("smooth_alpha", 1.0)))
        samples["experiment_id"] = exp
        samples["deployability"] = str(params["deployability"])
        samples["description"] = f"Phase4 {l_id} single-I raw-range {t_id}/{r_id}."
        tracks, summary = summarize(samples, exp, str(params["deployability"]), f"Phase4 {l_id} single-I raw-range row.", {"A": a_id, "R": r_id, "L": l_id, "I": i_id, "T": t_id, "kind": "range_fusion"})
        return {"job_index": job_index, "tracks": tracks, "summary": summary, "timing": {"experiment_id": exp, "seed_id": _SEED_ID, "wall_time_s": time.perf_counter() - t0, "status": "ok", "backend": "cuda", "device": device}}

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=gpu_workers) as pool:
        futures = [pool.submit(run_one, job) for job in jobs]
        for done_count, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            if done_count == 1 or done_count % 25 == 0 or done_count == len(jobs):
                elapsed = time.perf_counter() - started
                rate = done_count / elapsed if elapsed > 0 else float("nan")
                print(f"[phase4-full] raw_range_branch_gpu: {done_count}/{len(jobs)} done ({fmt(rate, 3)} rows/s)", flush=True)
    return sorted(rows, key=lambda r: int(r["job_index"]))


def collect(label: str, jobs: list[tuple], worker, workers: int, initializer, initargs: tuple) -> list[dict]:
    if not jobs:
        return []
    ctx = mp.get_context("fork")
    started = time.perf_counter()
    print(f"[phase4-full] {label}: {len(jobs)} rows with {workers} workers", flush=True)
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=initializer, initargs=initargs) as pool:
        futures = [pool.submit(worker, job) for job in jobs]
        for done_count, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            rows.append(result)
            if done_count == 1 or done_count % 25 == 0 or done_count == len(jobs):
                elapsed = time.perf_counter() - started
                rate = done_count / elapsed if elapsed > 0 else float("nan")
                print(f"[phase4-full] {label}: {done_count}/{len(jobs)} done ({fmt(rate, 3)} rows/s)", flush=True)
    return sorted(rows, key=lambda r: int(r["job_index"]))


def aggregate(summary_rows: list[dict]) -> list[dict]:
    for row in summary_rows:
        row["screening_score"] = S1.screening_score(row)
    out = sorted(summary_rows, key=lambda r: (float(r.get("screening_score", float("inf"))), float(r.get("trackmedian_err3d_p95_mm", float("inf")))))
    for idx, row in enumerate(out, start=1):
        row["rank"] = idx
    return out


def write_imu_stream_exports(run_dir: Path, imu_samples: dict[tuple[str, str, str], pd.DataFrame]) -> list[dict]:
    out_dir = run_dir / "imu_streams"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for (a_id, i_id, t_id), df in sorted(imu_samples.items()):
        file_name = f"phase4_imu_stream_{a_id}_{_SENSOR_ID}_{i_id}_{t_id}_{_SEED_ID}.csv.gz"
        rel_path = Path("imu_streams") / file_name
        export = df.copy()
        labels = [("seed_id", _SEED_ID), ("A", a_id), ("L", _SENSOR_ID), ("I", i_id), ("T", t_id)]
        for idx, (col, value) in enumerate(labels):
            if col in export.columns:
                export[col] = value
            else:
                export.insert(min(idx, len(export.columns)), col, value)
        export.to_csv(run_dir / rel_path, index=False, compression="gzip")
        manifest.append(
            {
                "seed_id": _SEED_ID,
                "A": a_id,
                "L": _SENSOR_ID,
                "I": i_id,
                "T": t_id,
                "rows": len(export),
                "capture_tags": int(export[["capture_id", "tag"]].drop_duplicates().shape[0]),
                "file": str(rel_path),
                "export_type": "imu_only_stream_with_raw_meas_bias_noise_vibration",
            }
        )
    write_csv(run_dir / "tables" / "phase4_imu_stream_manifest.csv", manifest)
    return manifest


def parse_corrected_imu_rows(spec: str) -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    for raw in str(spec).split(","):
        item = raw.strip().upper()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 5:
            raise ValueError(f"--corrected-imu-rows entries must look like A0:U4:P4:I5:T5; got {raw!r}")
        a_id, u_id, p_id, i_id, t_id = parts
        if a_id not in A_IDS or u_id not in U_IDS or p_id not in P_IDS or i_id not in I_IDS or t_id not in POS_T_IDS:
            raise ValueError(f"unknown corrected IMU row selector {raw!r}")
        if u_id not in CORRECTED_IMU_U_IDS:
            supported = ",".join(CORRECTED_IMU_U_IDS)
            raise ValueError(f"unsupported corrected IMU U selector {u_id!r}; supported: {supported}")
        rows.append((a_id, u_id, p_id, i_id, t_id))
    return rows


def summarize_corrected_imu(export: pd.DataFrame, labels: dict[str, str], rel_path: Path) -> dict:
    row = {
        **labels,
        "seed_id": _SEED_ID,
        "rows": len(export),
        "capture_tags": int(export[["capture_id", "tag"]].drop_duplicates().shape[0]),
        "file": str(rel_path),
        "export_type": "corrected_imu_proxy_capture_stream",
    }
    metrics = [
        "fused_err3d_mm",
        "imu_prior_err3d_mm",
        "correction_from_imu_prior_norm_mm",
        "imu_acc_correction_norm_proxy_mm_s2",
        "imu_acc_correction_ratio_proxy",
        "imu_gyro_correction_norm_proxy_dps",
        "imu_gyro_correction_ratio_proxy",
    ]
    for col in metrics:
        if col in export.columns:
            values = export[col].to_numpy(float)
            row[f"{col}_p50"] = S1.pct(values, 50)
            row[f"{col}_p95"] = S1.pct(values, 95)
            row[f"{col}_rms"] = rms(values)
    return row


def write_corrected_imu_exports(
    run_dir: Path,
    streams: dict[tuple[str, str, str], pd.DataFrame],
    imu_samples: dict[tuple[str, str, str], pd.DataFrame],
    row_spec: str,
) -> list[dict]:
    out_dir = run_dir / "corrected_imu_exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for a_id, u_id, p_id, i_id, t_id in parse_corrected_imu_rows(row_spec):
        if t_id == "T1":
            continue
        stream = streams[(a_id, u_id, p_id)]
        prior = imu_samples[(a_id, i_id, "T11")]
        params = POSITION_T_PARAMS[t_id]
        process = S1.li_process_factor(_SENSOR_ID, i_id)
        exp = f"X_{a_id}_{u_id}_{p_id}_{_SENSOR_ID}_{i_id}_{t_id}"
        fused = S1.position_fusion_samples(
            stream,
            prior,
            exp,
            str(params["deployability"]),
            f"Phase4 corrected IMU export {_SENSOR_ID}/{i_id}/{p_id}/{t_id}.",
            float(params["prior_sigma_base"]) * process,
            float(params["measurement_sigma"]),
        )
        keys = ["capture_id", "tag", "time_s"]
        prior_cols = keys + ["x_mm", "y_mm", "z_mm", "err3d_mm", "imu_only_endpoint_drift_3d_mm", "imu_only_drift_rate_3d_mm_s"]
        prior_view = prior[prior_cols].rename(
            columns={
                "x_mm": "imu_prior_x_mm",
                "y_mm": "imu_prior_y_mm",
                "z_mm": "imu_prior_z_mm",
                "err3d_mm": "imu_prior_err3d_mm",
            }
        )
        export = fused.merge(prior_view, on=keys, how="left")
        labels = [("seed_id", _SEED_ID), ("solver_row", exp), ("A", a_id), ("U", u_id), ("P", p_id), ("L", _SENSOR_ID), ("I", i_id), ("T", t_id)]
        for idx, (col, value) in enumerate(labels):
            if col in export.columns:
                export[col] = value
            else:
                export.insert(min(idx, len(export.columns)), col, value)
        export = export.rename(columns={"x_mm": "fused_x_mm", "y_mm": "fused_y_mm", "z_mm": "fused_z_mm", "err3d_mm": "fused_err3d_mm"})
        for axis in ["x", "y", "z"]:
            export[f"correction_from_imu_prior_{axis}_mm"] = export[f"fused_{axis}_mm"] - export[f"imu_prior_{axis}_mm"]
            export[f"correction_from_uwb_{axis}_mm"] = export[f"fused_{axis}_mm"] - export[f"uwb_{axis}_mm"]
        export["correction_from_imu_prior_norm_mm"] = np.linalg.norm(export[[f"correction_from_imu_prior_{a}_mm" for a in ["x", "y", "z"]]].to_numpy(float), axis=1)
        export["correction_from_uwb_norm_mm"] = np.linalg.norm(export[[f"correction_from_uwb_{a}_mm" for a in ["x", "y", "z"]]].to_numpy(float), axis=1)
        file_name = f"phase4_corrected_imu_{a_id}_{u_id}_{p_id}_{_SENSOR_ID}_{i_id}_{t_id}_{_SEED_ID}.csv.gz"
        rel_path = Path("corrected_imu_exports") / file_name
        export.to_csv(run_dir / rel_path, index=False, compression="gzip")
        manifest.append(summarize_corrected_imu(export, {"A": a_id, "U": u_id, "P": p_id, "L": _SENSOR_ID, "I": i_id, "T": t_id, "solver_row": exp}, rel_path))
    write_csv(run_dir / "tables" / "phase4_corrected_imu_export_manifest.csv", manifest)
    return manifest


def run(args: argparse.Namespace) -> dict:
    global _SEED_ID, _SENSOR_ID
    _SEED_ID = str(args.seed_id).upper()
    if not re.match(r"^S\d{2}$", _SEED_ID):
        raise ValueError(f"--seed-id must look like S00/S01/...; got {_SEED_ID!r}")
    _SENSOR_ID = str(args.sensor_id).upper()
    if not re.match(r"^L\d+$", _SENSOR_ID):
        raise ValueError(f"--sensor-id must look like L2/L16/L20; got {_SENSOR_ID!r}")
    L_IDS[:] = [_SENSOR_ID]
    run_id = args.run_id or f"phase4_{_SENSOR_ID}_singleI_TRUEFULL_{_SEED_ID}_1080ti_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = SIM_ROOT / "runs" / "phase4_algorithm_factory" / run_id
    for d in [run_dir / "logs", run_dir / "tables", run_dir / "reports", run_dir / "manifests", run_dir / "imu_streams", run_dir / "corrected_imu_exports"]:
        d.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    workers = max(1, min(int(args.workers), os.cpu_count() or 1))
    l_props, sensor_meta = install_sensor_props(_SENSOR_ID)
    sensor_name = str(sensor_meta[_SENSOR_ID].get("name", _SENSOR_ID))
    print(f"[phase4-full] run_id={run_id} L={_SENSOR_ID} ({sensor_name}) I=I0-I8 seed={_SEED_ID} workers={workers}", flush=True)

    full_manifest, exclusions, pos_jobs, raw_jobs, imu_jobs = build_full_manifest()
    write_csv(run_dir / "tables" / "phase4_full_manifest.csv", full_manifest)
    write_csv(run_dir / "tables" / "phase4_exclusion_reasons.csv", exclusions)
    write_json(
        run_dir / "manifest.json",
        {
            "run_id": run_id,
            "phase_status": "running",
            "phase": "phase4_singleI_TRUEFULL",
            "created_utc": datetime.now(UTC).isoformat(),
            "seed_count": 1,
            "seed_id": _SEED_ID,
            "L_ids": L_IDS,
            "I_ids": I_IDS,
            "declared_full_rows": len(full_manifest),
            "runnable_rows": len(pos_jobs) + len(raw_jobs) + len(imu_jobs),
            "excluded_rows": len(exclusions),
            "workers": workers,
            "raw_backend": args.raw_backend,
            "raw_device": args.raw_device,
            "raw_gpu_workers": int(args.raw_gpu_workers),
            "export_imu_streams": bool(args.export_imu_streams),
            "export_corrected_imu": bool(args.export_corrected_imu),
            "corrected_imu_rows": str(args.corrected_imu_rows),
            "host": {"platform": platform.platform(), "cpu_count": os.cpu_count(), "gpu": torch_cuda_info()},
            "git": git_status(),
        },
    )

    streams = load_streams()
    raw_by_track = S1.load_raw_frames(streams[("A0", "U4", "P0")])
    anchor_xyz, anchor_delay, tag_delay = S1.load_a0_layout()
    phase2_dir = SIM_ROOT / "runs" / "phase2_screening" / args.phase2_run
    range_bias, range_sigma = S1.load_range_policy(phase2_dir)

    summary_rows: list[dict] = []
    track_rows: list[dict] = []
    timing_rows: list[dict] = []
    imu_samples: dict[tuple[str, str, str], pd.DataFrame] = {}

    for result in collect("imu_diagnostics", imu_jobs, imu_worker, workers, init_imu_worker, (streams, l_props)):
        imu_samples[result["key"]] = result["samples"]
        summary_rows.append(result["summary"])
        track_rows.extend(result["tracks"])
        timing_rows.append(result["timing"])
        if len(summary_rows) % 25 == 0:
            write_csv(run_dir / "tables" / "phase4_summary_partial.csv", summary_rows)
            write_csv(run_dir / "tables" / "phase4_timing_partial.csv", timing_rows)

    for result in collect("position_branch", pos_jobs, position_worker, workers, init_position_worker, (streams, imu_samples, l_props)):
        summary_rows.append(result["summary"])
        track_rows.extend(result["tracks"])
        timing_rows.append(result["timing"])
        if len(summary_rows) % 50 == 0:
            write_csv(run_dir / "tables" / "phase4_summary_partial.csv", summary_rows)
            write_csv(run_dir / "tables" / "phase4_timing_partial.csv", timing_rows)

    use_gpu_raw = False
    if args.raw_backend in {"auto", "cuda"}:
        try:
            import torch

            use_gpu_raw = bool(torch.cuda.is_available() and str(args.raw_device).startswith("cuda"))
        except Exception:
            use_gpu_raw = False
        if args.raw_backend == "cuda" and not use_gpu_raw:
            raise RuntimeError("--raw-backend cuda requested, but CUDA is not available")

    if use_gpu_raw:
        raw_results = collect_raw_gpu(raw_jobs, raw_by_track, imu_samples, anchor_xyz, anchor_delay, tag_delay, range_bias, range_sigma, str(args.raw_device), int(args.raw_gpu_workers))
    else:
        raw_results = collect("raw_range_branch", raw_jobs, raw_worker, workers, init_raw_worker, (raw_by_track, imu_samples, anchor_xyz, anchor_delay, tag_delay, range_bias, range_sigma, l_props))

    for result in raw_results:
        summary_rows.append(result["summary"])
        track_rows.extend(result["tracks"])
        timing_rows.append(result["timing"])
        if len(summary_rows) % 25 == 0:
            write_csv(run_dir / "tables" / "phase4_summary_partial.csv", summary_rows)
            write_csv(run_dir / "tables" / "phase4_timing_partial.csv", timing_rows)

    imu_stream_manifest: list[dict] = []
    corrected_imu_manifest: list[dict] = []
    if args.export_imu_streams:
        imu_stream_manifest = write_imu_stream_exports(run_dir, imu_samples)
    if args.export_corrected_imu:
        corrected_imu_manifest = write_corrected_imu_exports(run_dir, streams, imu_samples, str(args.corrected_imu_rows))

    P1.add_baseline_deltas(summary_rows)
    ranked = aggregate(summary_rows)
    elapsed = time.perf_counter() - start
    write_csv(run_dir / "tables" / "phase4_summary.csv", summary_rows)
    write_csv(run_dir / "tables" / "phase4_track_metrics.csv", track_rows)
    write_csv(run_dir / "tables" / "phase4_timing.csv", timing_rows)
    write_csv(run_dir / "tables" / "phase4_full_ranking.csv", ranked)
    report = [
        f"# Phase4 {_SENSOR_ID} Single-I TRUEFULL {_SEED_ID}",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Run ID: `{run_id}`",
        f"Status: `complete`",
        f"Declared manifest rows: {len(full_manifest)}",
        f"Runnable rows completed: {len(summary_rows)}",
        f"Excluded rows: {len(exclusions)}",
        f"Wall time: {fmt(elapsed, 2)} s",
        "",
        "## Top 30",
        "",
        S1.markdown_table(ranked[:30], ["rank", "experiment_id", "kind", "A", "U", "P", "R", "L", "I", "T", "screening_score", "trackmedian_err3d_p50_mm", "trackmedian_err3d_p95_mm", "legacy_deltaR_error_rms_mm"]),
        "",
    ]
    report_name = f"PHASE4_{_SENSOR_ID}_SINGLEI_TRUEFULL_{_SEED_ID}.md"
    (run_dir / "reports" / report_name).write_text("\n".join(report), encoding="utf-8")
    write_json(
        run_dir / "manifest.json",
        {
            "run_id": run_id,
            "phase_status": "complete",
            "phase": "phase4_singleI_TRUEFULL",
            "created_utc": datetime.now(UTC).isoformat(),
            "elapsed_s": elapsed,
            "seed_count": 1,
            "seed_id": _SEED_ID,
            "L_ids": L_IDS,
            "I_ids": I_IDS,
            "declared_full_rows": len(full_manifest),
            "runnable_rows_completed": len(summary_rows),
            "excluded_rows": len(exclusions),
            "workers": workers,
            "raw_backend": "cuda" if use_gpu_raw else "cpu",
            "raw_device": str(args.raw_device),
            "raw_gpu_workers": int(args.raw_gpu_workers),
            "export_imu_streams": bool(args.export_imu_streams),
            "export_corrected_imu": bool(args.export_corrected_imu),
            "corrected_imu_rows": str(args.corrected_imu_rows),
            "imu_stream_exports": len(imu_stream_manifest),
            "corrected_imu_exports": len(corrected_imu_manifest),
            "sensor_metadata": sensor_meta,
            "outputs": {
                "manifest": "tables/phase4_full_manifest.csv",
                "ranking": "tables/phase4_full_ranking.csv",
                "summary": "tables/phase4_summary.csv",
                "track_metrics": "tables/phase4_track_metrics.csv",
                "exclusions": "tables/phase4_exclusion_reasons.csv",
                "imu_stream_manifest": "tables/phase4_imu_stream_manifest.csv" if args.export_imu_streams else "",
                "corrected_imu_export_manifest": "tables/phase4_corrected_imu_export_manifest.csv" if args.export_corrected_imu else "",
                "report": f"reports/{report_name}",
            },
            "host": {"platform": platform.platform(), "cpu_count": os.cpu_count(), "gpu": torch_cuda_info()},
            "git": git_status(),
        },
    )
    return {"run_id": run_id, "run_dir": str(run_dir), "rows": len(summary_rows), "declared_rows": len(full_manifest), "excluded_rows": len(exclusions), "elapsed_s": elapsed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase4 single-sensor single-I truefull factory.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--phase2-run", default="20260604T163422Z")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed-id", default="S00", help="Noise seed label, e.g. S00, S01, S02.")
    parser.add_argument("--sensor-id", default="L2", help="Sensor model from configs/sensors.yaml, e.g. L2, L16, L20.")
    parser.add_argument("--raw-backend", choices=["auto", "cpu", "cuda"], default="auto", help="Use CUDA for TRUEFULL raw_range_branch when available.")
    parser.add_argument("--raw-device", default="cuda:0", help="Torch device for --raw-backend cuda/auto.")
    parser.add_argument("--raw-gpu-workers", type=int, default=16, help="Concurrent CUDA feeders for TRUEFULL raw_range_branch.")
    parser.add_argument("--export-imu-streams", action=argparse.BooleanOptionalAction, default=True, help="Persist IMU-only streams with measured acc/gyro and simulated bias/noise/vibration channels.")
    parser.add_argument("--export-corrected-imu", action=argparse.BooleanOptionalAction, default=True, help="Persist selected capture-level corrected IMU proxy streams.")
    parser.add_argument("--corrected-imu-rows", default="A0:U4:P4:I5:T5", help="Comma-separated corrected export selectors, e.g. A0:U4:P4:I5:T5,A0:U4:P4:I6:T5.")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
