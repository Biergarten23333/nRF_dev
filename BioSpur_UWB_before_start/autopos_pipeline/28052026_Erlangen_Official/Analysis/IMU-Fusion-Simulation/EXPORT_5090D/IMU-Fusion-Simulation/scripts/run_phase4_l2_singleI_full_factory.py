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
from concurrent.futures import ProcessPoolExecutor, as_completed
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
    for d in [run_dir / "logs", run_dir / "tables", run_dir / "reports", run_dir / "manifests"]:
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

    for result in collect("raw_range_branch", raw_jobs, raw_worker, workers, init_raw_worker, (raw_by_track, imu_samples, anchor_xyz, anchor_delay, tag_delay, range_bias, range_sigma, l_props)):
        summary_rows.append(result["summary"])
        track_rows.extend(result["tracks"])
        timing_rows.append(result["timing"])
        if len(summary_rows) % 25 == 0:
            write_csv(run_dir / "tables" / "phase4_summary_partial.csv", summary_rows)
            write_csv(run_dir / "tables" / "phase4_timing_partial.csv", timing_rows)

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
            "sensor_metadata": sensor_meta,
            "outputs": {
                "manifest": "tables/phase4_full_manifest.csv",
                "ranking": "tables/phase4_full_ranking.csv",
                "summary": "tables/phase4_summary.csv",
                "track_metrics": "tables/phase4_track_metrics.csv",
                "exclusions": "tables/phase4_exclusion_reasons.csv",
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
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
