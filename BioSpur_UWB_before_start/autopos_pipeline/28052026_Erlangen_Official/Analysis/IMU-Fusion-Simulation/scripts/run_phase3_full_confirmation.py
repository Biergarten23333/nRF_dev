#!/usr/bin/env python3
"""Run Phase 3 full nominal multiseed confirmation."""

from __future__ import annotations

import argparse
import csv
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
import yaml


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
STAGE1_SCRIPT = SIM_ROOT / "scripts" / "run_phase2_stage1_screening.py"
SENSORS_YAML = SIM_ROOT / "configs" / "sensors.yaml"

DEFAULT_ACTIVE_L_IDS = ["L0", "L1", "L2", "L3", "L4", "L5", "L7", "L8"] + [f"L{i}" for i in range(10, 20)]
ACTIVE_L_IDS = list(DEFAULT_ACTIVE_L_IDS)
SINGLE_I_IDS = [f"I{i}" for i in range(0, 9)]
DECLARED_I_IDS = ["I0", "I1", "I3", "I4", "I7", "I8", "I1+I3+I7", "I1+I2+I3+I8"]
I_IDS = list(DECLARED_I_IDS)
UP_IDS = [("U4", "P0"), ("U4", "P2")]
R_IDS = ["R2", "R4"]
POS_T_IDS = ["T2", "T3", "T5"]
RANGE_T_IDS = ["T6", "T8"]

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


S1 = load_module(STAGE1_SCRIPT, "phase2_stage1_for_phase3")

_STREAMS: dict[tuple[str, str], pd.DataFrame] = {}
_B0: pd.DataFrame | None = None
_RAW_BY_TRACK: dict[tuple[str, str], pd.DataFrame] = {}
_IMU_BY_LI: dict[tuple[str, str], pd.DataFrame] = {}
_ANCHOR_XYZ: np.ndarray | None = None
_ANCHOR_DELAY: np.ndarray | None = None
_TAG_DELAY = 0.0
_RANGE_BIAS: np.ndarray | None = None
_RANGE_SIGMA: np.ndarray | None = None
_SEED_ID = ""
_SEED_RUN_ID = ""
_L_PROPS: dict[str, dict] = {}


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


def git_status() -> dict[str, object]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SIM_ROOT.parent, text=True).strip()
    except Exception:
        commit = "unknown"
    try:
        status = subprocess.check_output(["git", "status", "--short"], cwd=SIM_ROOT.parent, text=True).strip().splitlines()
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


def sensor_props_from_yaml() -> tuple[dict[str, dict], dict[str, dict]]:
    raw = yaml.safe_load(SENSORS_YAML.read_text(encoding="utf-8"))
    missing = [lid for lid in ACTIVE_L_IDS if lid not in raw]
    if missing:
        raise RuntimeError(f"missing active L sensor configs: {missing}")
    props: dict[str, dict] = {}
    for lid in ACTIVE_L_IDS:
        row = raw[lid]
        props[lid] = {
            "bias_mg": float(row["residual_accel_bias_mg"]),
            "noise_mg": float(row["accel_noise_mg"]),
            "rw_mg": float(row["accel_bias_random_walk_mg_sqrt_s"]),
            "vib_mg": float(row["vibration_sensitivity_mg"]),
            "extrinsic_mg": float(row["extrinsic_mg"]),
        }
    return props, {lid: raw[lid] for lid in ACTIVE_L_IDS}


def install_l_props(props: dict[str, dict]) -> None:
    S1.L_PROPS = props
    S1.I_MODS.update(EXTRA_I_MODS)


def resolve_l_ids(values: list[str] | None) -> list[str]:
    if not values:
        return list(DEFAULT_ACTIVE_L_IDS)
    known = set(DEFAULT_ACTIVE_L_IDS)
    out: list[str] = []
    for value in values:
        for raw in str(value).replace(",", " ").split():
            lid = raw.strip()
            if not lid:
                continue
            if lid not in known:
                raise ValueError(f"unknown or inactive L id {lid!r}; allowed={sorted(known)}")
            if lid not in out:
                out.append(lid)
    if not out:
        raise ValueError("--l-ids resolved to an empty set")
    return out


def resolve_i_ids(mode: str, values: list[str] | None) -> list[str]:
    if values:
        allowed = set(SINGLE_I_IDS) | set(DECLARED_I_IDS) | set(EXTRA_I_MODS)
        out: list[str] = []
        for value in values:
            for raw in str(value).replace(",", " ").split():
                iid = raw.strip()
                if not iid:
                    continue
                if iid not in allowed:
                    raise ValueError(f"unknown I id {iid!r}; allowed={sorted(allowed)}")
                if iid not in out:
                    out.append(iid)
        if not out:
            raise ValueError("--i-ids resolved to an empty set")
        return out
    if mode == "single":
        return list(SINGLE_I_IDS)
    if mode == "declared":
        return list(DECLARED_I_IDS)
    raise ValueError(f"unknown --i-mode {mode!r}")


def summarize(samples: pd.DataFrame, exp: str, deployability: str, description: str, labels: dict) -> tuple[list[dict], dict]:
    tracks, summary = S1.summarize_experiment(samples, exp, deployability, description, labels)
    seed_id = labels.get("seed_id", "")
    for row in tracks:
        row["seed_id"] = seed_id
    summary["seed_id"] = seed_id
    summary["experiment_uid"] = f"{exp}__{seed_id}" if seed_id else exp
    return tracks, summary


def init_position_worker(streams, imu_by_li, l_props, seed_id):
    global _STREAMS, _IMU_BY_LI, _L_PROPS, _SEED_ID
    _STREAMS = streams
    _IMU_BY_LI = imu_by_li
    _L_PROPS = l_props
    _SEED_ID = seed_id
    install_l_props(l_props)


def init_imu_worker(b0, l_props, seed_run_id, seed_id):
    global _B0, _L_PROPS, _SEED_RUN_ID, _SEED_ID
    _B0 = b0
    _L_PROPS = l_props
    _SEED_RUN_ID = seed_run_id
    _SEED_ID = seed_id
    install_l_props(l_props)


def init_range_worker(raw_by_track, imu_by_li, anchor_xyz, anchor_delay, tag_delay, range_bias, range_sigma, l_props, seed_id):
    global _RAW_BY_TRACK, _IMU_BY_LI, _ANCHOR_XYZ, _ANCHOR_DELAY, _TAG_DELAY, _RANGE_BIAS, _RANGE_SIGMA
    global _L_PROPS, _SEED_ID
    _RAW_BY_TRACK = raw_by_track
    _IMU_BY_LI = imu_by_li
    _ANCHOR_XYZ = anchor_xyz
    _ANCHOR_DELAY = anchor_delay
    _TAG_DELAY = tag_delay
    _RANGE_BIAS = range_bias
    _RANGE_SIGMA = range_sigma
    _L_PROPS = l_props
    _SEED_ID = seed_id
    install_l_props(l_props)


def imu_worker(job: tuple[int, str, str]) -> dict:
    job_index, l_id, i_id = job
    if _B0 is None:
        raise RuntimeError("IMU worker missing B0")
    t0 = time.perf_counter()
    imu = S1.simulate_imu_for_li(_B0, _SEED_RUN_ID, l_id, i_id)
    exp = f"X_A0_{l_id}_{i_id}_T11"
    tracks, summary = summarize(
        imu,
        exp,
        "imu_only_diagnostic_oracle" if l_id == "L0" else "imu_only_diagnostic_screening",
        f"Phase 3 IMU-only drift diagnostic {l_id}/{i_id}/{_SEED_ID}.",
        {"A": "A0", "L": l_id, "I": i_id, "T": "T11", "kind": "imu_only", "seed_id": _SEED_ID},
    )
    return {
        "job_index": job_index,
        "L": l_id,
        "I": i_id,
        "imu": imu,
        "tracks": tracks,
        "summary": summary,
        "timing": {"experiment_id": exp, "seed_id": _SEED_ID, "wall_time_s": time.perf_counter() - t0, "status": "ok"},
    }


def position_worker(job: tuple[int, str, str, str, str, str]) -> dict:
    job_index, u_id, p_id, l_id, i_id, t_id = job
    t0 = time.perf_counter()
    stream = _STREAMS[(u_id, p_id)]
    prior = _IMU_BY_LI[(l_id, i_id)]
    params = S1.T_PARAMS[t_id]
    process = S1.li_process_factor(l_id, i_id)
    prior_sigma = float(params["prior_sigma_base"]) * process
    if l_id == "L0":
        prior_sigma = min(prior_sigma, 8.0 if t_id == "T2" else 35.0)
    exp = f"X_A0_{u_id}_{p_id}_{l_id}_{i_id}_{t_id}"
    samples = S1.position_fusion_samples(
        stream,
        prior,
        exp,
        str(params["deployability"]),
        f"Phase 3 nominal position-side {t_id} row with {u_id}/{p_id}/{l_id}/{i_id}/{_SEED_ID}.",
        prior_sigma,
        float(params["measurement_sigma"]),
    )
    tracks, summary = summarize(
        samples,
        exp,
        str(params["deployability"]),
        "Phase 3 nominal position-side fusion row.",
        {"A": "A0", "U": u_id, "P": p_id, "L": l_id, "I": i_id, "T": t_id, "kind": "position_fusion", "seed_id": _SEED_ID},
    )
    return {"job_index": job_index, "tracks": tracks, "summary": summary, "timing": {"experiment_id": exp, "seed_id": _SEED_ID, "wall_time_s": time.perf_counter() - t0, "status": "ok"}}


def range_worker(job: tuple[int, str, str, str, str]) -> dict:
    job_index, r_id, l_id, i_id, t_id = job
    t0 = time.perf_counter()
    if _ANCHOR_XYZ is None or _ANCHOR_DELAY is None or _RANGE_BIAS is None or _RANGE_SIGMA is None:
        raise RuntimeError("range worker missing state")
    prior = _IMU_BY_LI[(l_id, i_id)]
    params = S1.T_PARAMS[t_id]
    process = S1.li_process_factor(l_id, i_id)
    prior_sigma = float(params["prior_sigma_base"]) * process
    if l_id == "L0":
        prior_sigma = min(prior_sigma, 45.0)
    exp = f"X_A0_{r_id}_{l_id}_{i_id}_{t_id}"
    samples = S1.range_fusion_samples(
        _RAW_BY_TRACK,
        prior,
        exp,
        str(params["deployability"]),
        f"Phase 3 nominal range-side {t_id} row with {r_id}/{l_id}/{i_id}/{_SEED_ID}.",
        prior_sigma,
        1.0 if r_id == "R2" else 1.35,
        t_id == "T8" or r_id == "R4",
        _RANGE_BIAS,
        _RANGE_SIGMA,
        _ANCHOR_XYZ,
        _ANCHOR_DELAY,
        _TAG_DELAY,
    )
    tracks, summary = summarize(
        samples,
        exp,
        str(params["deployability"]),
        "Phase 3 nominal range-side fusion row.",
        {"A": "A0", "R": r_id, "L": l_id, "I": i_id, "T": t_id, "kind": "range_fusion", "seed_id": _SEED_ID},
    )
    return {"job_index": job_index, "tracks": tracks, "summary": summary, "timing": {"experiment_id": exp, "seed_id": _SEED_ID, "wall_time_s": time.perf_counter() - t0, "status": "ok"}}


def collect_parallel(label: str, jobs: list[tuple], worker, workers: int, initializer, initargs: tuple) -> list[dict]:
    ctx = mp.get_context("fork")
    started = time.perf_counter()
    results: list[dict] = []
    print(f"[phase3] {label}: {len(jobs)} rows with {workers} workers", flush=True)
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=initializer, initargs=initargs) as pool:
        futures = [pool.submit(worker, job) for job in jobs]
        for done_count, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            if done_count == 1 or done_count % 50 == 0 or done_count == len(jobs):
                elapsed = time.perf_counter() - started
                rate = done_count / elapsed if elapsed > 0 else float("nan")
                print(f"[phase3] {label}: {done_count}/{len(jobs)} done ({fmt(rate, 2)} rows/s)", flush=True)
    return sorted(results, key=lambda r: int(r["job_index"]))


def aggregate_multiseed(summary_rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(summary_rows)
    metric_cols = [
        "trackmedian_err3d_p50_mm",
        "trackmedian_err3d_p95_mm",
        "legacy_deltaR_error_rms_mm",
        "trackmedian_radius_error_abs_mm",
        "screening_score",
    ]
    group_cols = ["experiment_id", "kind", "A", "U", "P", "R", "L", "I", "T"]
    present_group_cols = [c for c in group_cols if c in df.columns]
    rows = []
    for keys, g in df.groupby(present_group_cols, dropna=False, sort=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {col: val for col, val in zip(present_group_cols, keys)}
        row["seed_count"] = int(g["seed_id"].nunique()) if "seed_id" in g else 0
        for col in metric_cols:
            if col not in g:
                continue
            vals = pd.to_numeric(g[col], errors="coerce").to_numpy(float)
            row[f"{col}_mean"] = float(np.nanmean(vals))
            row[f"{col}_std"] = float(np.nanstd(vals))
            row[f"{col}_min"] = float(np.nanmin(vals))
            row[f"{col}_max"] = float(np.nanmax(vals))
        rows.append(row)
    rows.sort(key=lambda r: (float(r.get("screening_score_mean", float("inf"))), float(r.get("trackmedian_err3d_p95_mm_mean", float("inf")))))
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx
    return rows


def run_seed(seed_index: int, run_id: str, workers: int, run_dir: Path, shared: dict, l_props: dict) -> dict:
    seed_id = f"S{seed_index:02d}"
    seed_run_id = f"{run_id}_{seed_id}"
    seed_dir = run_dir / "stage1_full_nominal_multiseed" / "seeds" / seed_id
    for d in [seed_dir / "tables", seed_dir / "logs"]:
        d.mkdir(parents=True, exist_ok=True)

    b0 = shared["b0"]
    streams = shared["streams"]
    raw_by_track = shared["raw_by_track"]
    anchor_xyz = shared["anchor_xyz"]
    anchor_delay = shared["anchor_delay"]
    tag_delay = shared["tag_delay"]
    range_bias = shared["range_bias"]
    range_sigma = shared["range_sigma"]

    print(f"[phase3] seed {seed_id}: generating IMU priors for {len(ACTIVE_L_IDS) * len(I_IDS)} L/I rows", flush=True)
    imu_by_li: dict[tuple[str, str], pd.DataFrame] = {}
    summary_rows: list[dict] = []
    track_rows: list[dict] = []
    timing_rows: list[dict] = []

    # Baseline is repeated per seed so seed-level tables are self-contained.
    t0 = time.perf_counter()
    tracks, summary = summarize(
        streams[("U4", "P0")],
        "B0_A0_U4_P0_T1",
        "online_deployable_uwb_only",
        "Phase 3 repeated B0 control.",
        {"A": "A0", "U": "U4", "P": "P0", "T": "T1", "kind": "baseline", "seed_id": seed_id},
    )
    track_rows.extend(tracks)
    summary_rows.append(summary)
    timing_rows.append({"experiment_id": summary["experiment_id"], "seed_id": seed_id, "wall_time_s": time.perf_counter() - t0, "status": "ok"})

    imu_jobs: list[tuple[int, str, str]] = []
    for l_id in ACTIVE_L_IDS:
        for i_id in I_IDS:
            imu_jobs.append((len(imu_jobs), l_id, i_id))
    for result in collect_parallel(
        "imu_prior " + seed_id,
        imu_jobs,
        imu_worker,
        workers,
        init_imu_worker,
        (b0, l_props, seed_run_id, seed_id),
    ):
        imu_by_li[(result["L"], result["I"])] = result["imu"]
        track_rows.extend(result["tracks"])
        summary_rows.append(result["summary"])
        timing_rows.append(result["timing"])

    position_jobs: list[tuple[int, str, str, str, str, str]] = []
    for (u_id, p_id) in UP_IDS:
        for l_id in ACTIVE_L_IDS:
            for i_id in I_IDS:
                for t_id in POS_T_IDS:
                    position_jobs.append((len(position_jobs), u_id, p_id, l_id, i_id, t_id))
    for result in collect_parallel("position_fusion " + seed_id, position_jobs, position_worker, workers, init_position_worker, (streams, imu_by_li, l_props, seed_id)):
        track_rows.extend(result["tracks"])
        summary_rows.append(result["summary"])
        timing_rows.append(result["timing"])

    range_jobs: list[tuple[int, str, str, str, str]] = []
    for r_id in R_IDS:
        for l_id in ACTIVE_L_IDS:
            for i_id in I_IDS:
                for t_id in RANGE_T_IDS:
                    range_jobs.append((len(range_jobs), r_id, l_id, i_id, t_id))
    for result in collect_parallel(
        "range_fusion " + seed_id,
        range_jobs,
        range_worker,
        workers,
        init_range_worker,
        (raw_by_track, imu_by_li, anchor_xyz, anchor_delay, tag_delay, range_bias, range_sigma, l_props, seed_id),
    ):
        track_rows.extend(result["tracks"])
        summary_rows.append(result["summary"])
        timing_rows.append(result["timing"])

    S1.P1.add_baseline_deltas(summary_rows)
    for row in summary_rows:
        row["screening_score"] = S1.screening_score(row)
    write_csv(seed_dir / "tables" / "phase3_seed_summary.csv", summary_rows)
    write_csv(seed_dir / "tables" / "phase3_seed_track_metrics.csv", track_rows)
    write_csv(seed_dir / "tables" / "phase3_seed_timing.csv", timing_rows)
    return {"seed_id": seed_id, "summary_rows": summary_rows, "track_rows": track_rows, "timing_rows": timing_rows}


def run(args: argparse.Namespace) -> dict:
    global ACTIVE_L_IDS, I_IDS
    ACTIVE_L_IDS = resolve_l_ids(args.l_ids)
    I_IDS = resolve_i_ids(args.i_mode, args.i_ids)
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_subdir = str(getattr(args, "output_subdir", "phase3_full_confirmation") or "phase3_full_confirmation")
    phase_name = str(getattr(args, "phase_name", "phase3_full_confirmation") or "phase3_full_confirmation")
    run_dir = SIM_ROOT / "runs" / output_subdir / run_id
    for d in [run_dir / "stage0_readiness_and_matrix_manifest" / "tables", run_dir / "stage1_full_nominal_multiseed" / "tables", run_dir / "tables", run_dir / "reports"]:
        d.mkdir(parents=True, exist_ok=True)

    l_props, sensor_metadata = sensor_props_from_yaml()
    install_l_props(l_props)
    cpu_count = os.cpu_count() or 2
    workers = max(1, min(int(args.workers or min(10, max(2, cpu_count - 2))), cpu_count))
    seeds = int(args.seeds)
    print(f"[phase3] run_id={run_id} seeds={seeds} workers={workers} active_L={len(ACTIVE_L_IDS)}", flush=True)

    start = time.perf_counter()
    b0 = S1.load_b0_samples()
    p2 = S1.robust_p2_stream(b0)
    raw_by_track = S1.load_raw_frames(b0)
    anchor_xyz, anchor_delay, tag_delay = S1.load_a0_layout()
    phase2_run = args.phase2_run or "20260604T163422Z"
    phase2_dir = SIM_ROOT / "runs" / "phase2_screening" / phase2_run
    range_bias, range_sigma = S1.load_range_policy(phase2_dir)
    shared = {
        "b0": b0,
        "streams": {("U4", "P0"): b0.copy(), ("U4", "P2"): p2},
        "raw_by_track": raw_by_track,
        "anchor_xyz": anchor_xyz,
        "anchor_delay": anchor_delay,
        "tag_delay": tag_delay,
        "range_bias": range_bias,
        "range_sigma": range_sigma,
    }

    position_rows_per_seed = len(UP_IDS) * len(ACTIVE_L_IDS) * len(I_IDS) * len(POS_T_IDS)
    range_rows_per_seed = len(R_IDS) * len(ACTIVE_L_IDS) * len(I_IDS) * len(RANGE_T_IDS)
    imu_rows_per_seed = len(ACTIVE_L_IDS) * len(I_IDS)
    rows_per_seed = 1 + imu_rows_per_seed + position_rows_per_seed + range_rows_per_seed
    matrix = {
        "run_id": run_id,
        "phase": phase_name,
        "scope": "A0_only_minimum_runnable_nominal_multiseed",
        "active_L_ids": ACTIVE_L_IDS,
        "I_ids": I_IDS,
        "I_mode": args.i_mode,
        "UP_ids": [f"{u}/{p}" for u, p in UP_IDS],
        "R_ids": R_IDS,
        "position_T_ids": POS_T_IDS,
        "range_T_ids": RANGE_T_IDS,
        "seeds": seeds,
        "rows_per_seed": rows_per_seed,
        "total_rows": rows_per_seed * seeds,
        "position_rows_per_seed": position_rows_per_seed,
        "range_rows_per_seed": range_rows_per_seed,
        "imu_rows_per_seed": imu_rows_per_seed,
        "source_missing_exclusions": [
            {"A": "A1", "reason": "NOT_RUN_SOURCE_MISSING", "detail": "Phase3 runner currently has A0 v4-io layout and U4/P/R artifacts only."},
            {"A": "A2", "reason": "NOT_RUN_SOURCE_MISSING", "detail": "Phase3 runner currently has A0 v4-io layout and U4/P/R artifacts only."},
            {"U": "U1-U3", "reason": "NOT_RUN_SOURCE_MISSING", "detail": "Only U4 solved-position sample stream exists in this runner."},
            {"P": "P1/P3/P4/P5", "reason": "NOT_IMPLEMENTED_IN_THIS_RUNNER", "detail": "This runner currently provides P0 and P2 only."},
            {"R": "R0/R1/R3", "reason": "NOT_IMPLEMENTED_IN_THIS_RUNNER", "detail": "This runner currently provides R2 and R4 only."},
            {"T": "T4/T7/T9/T10/T12", "reason": "NOT_IMPLEMENTED_IN_THIS_RUNNER", "detail": "This runner currently provides T2/T3/T5/T6/T8/T11 plus B0/T1 control."},
        ],
    }
    write_json(run_dir / "stage0_readiness_and_matrix_manifest" / "matrix_manifest.json", matrix)
    write_csv(run_dir / "stage0_readiness_and_matrix_manifest" / "tables" / "sensor_metadata.csv", [{"L": k, **v} for k, v in sensor_metadata.items()])
    write_csv(run_dir / "stage0_readiness_and_matrix_manifest" / "tables" / "exclusion_reasons.csv", matrix["source_missing_exclusions"])

    all_summary: list[dict] = []
    all_tracks: list[dict] = []
    all_timing: list[dict] = []
    for seed_index in range(seeds):
        seed_result = run_seed(seed_index, run_id, workers, run_dir, shared, l_props)
        all_summary.extend(seed_result["summary_rows"])
        all_tracks.extend(seed_result["track_rows"])
        all_timing.extend(seed_result["timing_rows"])
        write_csv(run_dir / "stage1_full_nominal_multiseed" / "tables" / "phase3_nominal_summary_partial.csv", all_summary)
        write_csv(run_dir / "stage1_full_nominal_multiseed" / "tables" / "phase3_nominal_timing_partial.csv", all_timing)

    aggregate = aggregate_multiseed(all_summary)
    elapsed = time.perf_counter() - start
    write_csv(run_dir / "stage1_full_nominal_multiseed" / "tables" / "phase3_nominal_summary.csv", all_summary)
    write_csv(run_dir / "stage1_full_nominal_multiseed" / "tables" / "phase3_nominal_track_metrics.csv", all_tracks)
    write_csv(run_dir / "stage1_full_nominal_multiseed" / "tables" / "phase3_nominal_timing.csv", all_timing)
    write_csv(run_dir / "tables" / "phase3_nominal_summary.csv", all_summary)
    write_csv(run_dir / "tables" / "phase3_final_ranking.csv", aggregate)
    write_csv(run_dir / "tables" / "phase3_exclusion_reasons.csv", matrix["source_missing_exclusions"])
    report = [
        "# Phase 4 L2 Single-I Current-Implementation Accuracy Run" if phase_name.startswith("phase4") else "# Phase 3 Full Confirmation",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Run ID: `{run_id}`",
        f"Phase status: `stage1_full_nominal_multiseed_complete`",
        f"Rows: {len(all_summary)}",
        f"Seeds: {seeds}",
        f"Workers: {workers}",
        f"Wall time: {fmt(elapsed, 2)} s",
        "",
        "## Top 20 Nominal Multiseed Ranking",
        "",
    ]
    cols = ["rank", "experiment_id", "kind", "L", "I", "T", "seed_count", "screening_score_mean", "trackmedian_err3d_p50_mm_mean", "trackmedian_err3d_p95_mm_mean", "legacy_deltaR_error_rms_mm_mean"]
    report.append(S1.markdown_table(aggregate[:20], cols))
    report.append("")
    report_name = "PHASE4_CURRENT_IMPLEMENTATION_ACCURACY.md" if phase_name.startswith("phase4") else "PHASE3_FULL_CONFIRMATION.md"
    (run_dir / "reports" / report_name).write_text("\n".join(report), encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "phase_status": "stage1_full_nominal_multiseed_complete",
        "phase": phase_name,
        "stage_completed": "stage1_full_nominal_multiseed",
        "scope": "A0_only_minimum_runnable_nominal_multiseed",
        "created_utc": datetime.now(UTC).isoformat(),
        "elapsed_s": elapsed,
        "workers": workers,
        "rows": len(all_summary),
        "track_metric_rows": len(all_tracks),
        "matrix": matrix,
        "host": {"platform": platform.platform(), "cpu_count": cpu_count, "gpu": torch_cuda_info()},
        "git": git_status(),
        "outputs": {
            "phase3_nominal_summary": "tables/phase3_nominal_summary.csv",
            "phase3_final_ranking": "tables/phase3_final_ranking.csv",
            "phase3_exclusion_reasons": "tables/phase3_exclusion_reasons.csv",
            "phase_report": f"reports/{report_name}",
        },
    }
    write_json(run_dir / "manifest.json", manifest)
    manifest_prefix = "phase4" if phase_name.startswith("phase4") else "phase3"
    write_json(SIM_ROOT / "manifests" / f"{manifest_prefix}_{run_id}.json", manifest)
    return {"run_id": run_id, "run_dir": str(run_dir), "rows": len(all_summary), "elapsed_s": elapsed, "top": aggregate[:10]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 3 full confirmation.")
    parser.add_argument("--run-id", default="", help="Run ID. Defaults to UTC timestamp.")
    parser.add_argument("--phase2-run", default="20260604T163422Z", help="Phase2 run providing range bias policy.")
    parser.add_argument("--seeds", type=int, default=5, help="Noise seeds for nominal multiseed.")
    parser.add_argument("--workers", type=int, default=0, help="CPU worker processes. Default: min(10, cpu_count - 2).")
    parser.add_argument("--l-ids", nargs="+", default=None, help="Active L ids, e.g. --l-ids L2.")
    parser.add_argument("--i-mode", choices=["declared", "single"], default="declared", help="declared = legacy declared I rows; single = I0-I8 only.")
    parser.add_argument("--i-ids", nargs="+", default=None, help="Override active I ids.")
    parser.add_argument("--output-subdir", default="phase3_full_confirmation", help="Subdirectory under runs/ for outputs.")
    parser.add_argument("--phase-name", default="phase3_full_confirmation", help="Phase name written to manifest/report.")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({"run_id": result["run_id"], "run_dir": result["run_dir"], "rows": result["rows"], "elapsed_s": result["elapsed_s"]}, indent=2))
    print("\nTOP 10")
    for row in result["top"]:
        print(
            f"#{row['rank']} {row['experiment_id']} score_mean={fmt(row.get('screening_score_mean'))} "
            f"P50={fmt(row.get('trackmedian_err3d_p50_mm_mean'))} P95={fmt(row.get('trackmedian_err3d_p95_mm_mean'))} "
            f"dR={fmt(row.get('legacy_deltaR_error_rms_mm_mean'))}"
        )


if __name__ == "__main__":
    main()
