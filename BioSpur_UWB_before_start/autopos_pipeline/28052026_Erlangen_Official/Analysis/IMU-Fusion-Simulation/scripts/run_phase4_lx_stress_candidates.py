#!/usr/bin/env python3
"""Run stress tests for the L2/L16/L20 Phase 4 winning families.

This is not a replacement for the TRUEFULL solver factory.  It is a robustness
follow-up: keep the ROTO data, A0/U4 source, and selected high-value P/I/T
families fixed, then multiply the IMU residual error terms to see which sensor
and solver combination survives vibration, bias/random-walk, mounting error,
and a combined harsh case.
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

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
ANALYSIS_ROOT = SIM_ROOT.parent
OFFICIAL_ROOT = ANALYSIS_ROOT.parent
FULL_SCRIPT = SIM_ROOT / "scripts" / "run_phase4_l2_singleI_full_factory.py"
SENSORS_YAML = SIM_ROOT / "configs" / "sensors.yaml"
OUT_BASE = SIM_ROOT / "runs" / "phase4_stress"

DEFAULT_SENSORS = ["L2", "L16", "L20"]
DEFAULT_SEEDS = ["S00", "S01", "S02", "S03", "S04"]
DEFAULT_P_IDS = ["P0", "P4", "P5"]
DEFAULT_I_IDS = ["I3", "I5"]
DEFAULT_T_IDS = ["T2", "T4"]

STRESS_CASES: dict[str, dict[str, float | str]] = {
    "ST0_nominal": {
        "description": "control: datasheet/residual parameters unchanged",
        "bias": 1.0,
        "noise": 1.0,
        "rw": 1.0,
        "vib": 1.0,
        "extrinsic": 1.0,
    },
    "ST1_vibration_3x": {
        "description": "motor/body vibration sensitivity multiplied by 3",
        "bias": 1.0,
        "noise": 1.0,
        "rw": 1.0,
        "vib": 3.0,
        "extrinsic": 1.0,
    },
    "ST2_bias_rw_2x": {
        "description": "residual accel bias and bias random-walk multiplied by 2",
        "bias": 2.0,
        "noise": 1.0,
        "rw": 2.0,
        "vib": 1.0,
        "extrinsic": 1.0,
    },
    "ST3_extrinsic_4x": {
        "description": "IMU/body mounting or frame residual multiplied by 4",
        "bias": 1.0,
        "noise": 1.0,
        "rw": 1.0,
        "vib": 1.0,
        "extrinsic": 4.0,
    },
    "ST4_harsh_combo": {
        "description": "combined bad case: bias 2.5x, noise 1.5x, rw 3x, vibration 4x, extrinsic 4x",
        "bias": 2.5,
        "noise": 1.5,
        "rw": 3.0,
        "vib": 4.0,
        "extrinsic": 4.0,
    },
}

PROP_MULTIPLIER_KEYS = {
    "bias_mg": "bias",
    "noise_mg": "noise",
    "rw_mg": "rw",
    "vib_mg": "vib",
    "extrinsic_mg": "extrinsic",
}

SENSOR_LABEL = {
    "L2": "L2 MPU6050/JY61P-like",
    "L16": "L16 ICM-45686",
    "L20": "L20 Xsens MTi-3",
}

METRIC = "trackmedian_err3d_p95_mm"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


F = load_module(FULL_SCRIPT, "phase4_full_for_lx_stress")
S1 = F.S1
P1 = F.P1

_STREAMS: dict[tuple[str, str, str], pd.DataFrame] = {}
_SENSOR_META: dict[str, dict] = {}
_P_IDS: list[str] = []
_T_IDS: list[str] = []


def fmt(value: object, digits: int = 1) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(f):
        return "nan"
    return f"{f:.{digits}f}"


def split_arg(value: str, allowed: list[str] | None = None) -> list[str]:
    items = [v.strip().upper() for v in value.split(",") if v.strip()]
    if allowed is not None:
        bad = [v for v in items if v not in allowed]
        if bad:
            raise ValueError(f"unsupported values {bad}; allowed={allowed}")
    return items


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


def md_table(df: pd.DataFrame, columns: list[str], max_rows: int = 20) -> str:
    rows = df.head(max_rows)
    if rows.empty:
        return ""
    out = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for _, row in rows.iterrows():
        vals: list[str] = []
        for col in columns:
            val = row.get(col, "")
            vals.append(fmt(val) if isinstance(val, (float, np.floating)) else str(val))
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


def load_sensor_meta(sensor_ids: list[str]) -> dict[str, dict]:
    raw = yaml.safe_load(SENSORS_YAML.read_text(encoding="utf-8"))
    missing = [sid for sid in sensor_ids if sid not in raw]
    if missing:
        raise KeyError(f"missing sensors in {SENSORS_YAML}: {missing}")
    return {sid: raw[sid] for sid in sensor_ids}


def sensor_props(sensor_id: str, stress_id: str, meta: dict[str, dict]) -> dict[str, dict]:
    row = meta[sensor_id]
    props = {
        sensor_id: {
            "bias_mg": float(row["residual_accel_bias_mg"]),
            "noise_mg": float(row["accel_noise_mg"]),
            "rw_mg": float(row["accel_bias_random_walk_mg_sqrt_s"]),
            "vib_mg": float(row["vibration_sensitivity_mg"]),
            "extrinsic_mg": float(row["extrinsic_mg"]),
        }
    }
    stress = STRESS_CASES[stress_id]
    for prop_key, mult_key in PROP_MULTIPLIER_KEYS.items():
        props[sensor_id][prop_key] *= float(stress[mult_key])
    return props


def summarize(samples: pd.DataFrame, experiment_id: str, deployability: str, description: str, labels: dict) -> tuple[list[dict], dict]:
    tracks, summary = S1.summarize_experiment(samples, experiment_id, deployability, description, labels)
    for row in tracks:
        row.update(labels)
    summary.update(labels)
    return tracks, summary


def build_baselines(streams: dict[tuple[str, str, str], pd.DataFrame], p_ids: list[str]) -> tuple[list[dict], list[dict], dict[str, dict]]:
    tracks: list[dict] = []
    summaries: list[dict] = []
    by_p: dict[str, dict] = {}
    for p_id in p_ids:
        exp = f"X_A0_U4_{p_id}_T1"
        samples = streams[("A0", "U4", p_id)].copy()
        samples["experiment_id"] = exp
        samples["deployability"] = "uwb_only_control"
        samples["description"] = f"A0/U4/{p_id}/T1 pure UWB control."
        labels = {"A": "A0", "U": "U4", "P": p_id, "L": "B0", "I": "I0", "T": "T1", "kind": "uwb_only"}
        t_rows, s_row = summarize(samples, exp, "uwb_only_control", f"A0/U4/{p_id}/T1 pure UWB control.", labels)
        tracks.extend(t_rows)
        summaries.append(s_row)
        by_p[p_id] = s_row
    return tracks, summaries, by_p


def init_worker(streams, sensor_meta, p_ids, t_ids) -> None:
    global _STREAMS, _SENSOR_META, _P_IDS, _T_IDS
    _STREAMS = streams
    _SENSOR_META = sensor_meta
    _P_IDS = p_ids
    _T_IDS = t_ids
    S1.I_MODS.update(F.EXTRA_I_MODS)


def worker(job: tuple[int, str, str, str, str]) -> dict:
    job_index, sensor_id, seed_id, stress_id, i_id = job
    started = time.perf_counter()
    F._SEED_ID = seed_id
    props = sensor_props(sensor_id, stress_id, _SENSOR_META)
    S1.L_PROPS = props
    S1.I_MODS.update(F.EXTRA_I_MODS)

    base = _STREAMS[("A0", "U4", "P0")]
    run_key = f"phase4_stress_{sensor_id}_{seed_id}_{stress_id}_A0"
    prior = S1.simulate_imu_for_li(base, run_key, sensor_id, i_id)

    track_rows: list[dict] = []
    summary_rows: list[dict] = []
    timing_rows: list[dict] = []
    imu_exp = f"X_A0_{sensor_id}_{i_id}_T11__{stress_id}__{seed_id}"
    prior = prior.copy()
    prior["experiment_id"] = imu_exp
    prior["deployability"] = "imu_only_stress_diagnostic"
    prior["description"] = f"{sensor_id}/{i_id} IMU-only stress diagnostic {stress_id}/{seed_id}."
    labels = {
        "A": "A0",
        "L": sensor_id,
        "I": i_id,
        "T": "T11",
        "P": "",
        "U": "",
        "stress_id": stress_id,
        "seed_id": seed_id,
        "kind": "imu_only",
    }
    t_rows, s_row = summarize(prior, imu_exp, "imu_only_stress_diagnostic", prior["description"].iloc[0], labels)
    track_rows.extend(t_rows)
    summary_rows.append(s_row)

    for p_id in _P_IDS:
        stream = _STREAMS[("A0", "U4", p_id)]
        for t_id in _T_IDS:
            params = F.POSITION_T_PARAMS[t_id]
            process = S1.li_process_factor(sensor_id, i_id)
            exp = f"X_A0_U4_{p_id}_{sensor_id}_{i_id}_{t_id}"
            row_t0 = time.perf_counter()
            samples = S1.position_fusion_samples(
                stream,
                prior,
                exp,
                str(params["deployability"]),
                f"Stress {stress_id}/{seed_id}: A0/U4/{p_id}/{sensor_id}/{i_id}/{t_id}.",
                float(params["prior_sigma_base"]) * process,
                float(params["measurement_sigma"]),
            )
            labels = {
                "A": "A0",
                "U": "U4",
                "P": p_id,
                "L": sensor_id,
                "I": i_id,
                "T": t_id,
                "stress_id": stress_id,
                "seed_id": seed_id,
                "kind": "position_fusion",
            }
            t_rows, s_row = summarize(
                samples,
                exp,
                str(params["deployability"]),
                f"Stress {stress_id}/{seed_id}: A0/U4/{p_id}/{sensor_id}/{i_id}/{t_id}.",
                labels,
            )
            track_rows.extend(t_rows)
            summary_rows.append(s_row)
            timing_rows.append(
                {
                    "job_index": job_index,
                    "experiment_id": exp,
                    "L": sensor_id,
                    "I": i_id,
                    "P": p_id,
                    "T": t_id,
                    "seed_id": seed_id,
                    "stress_id": stress_id,
                    "wall_time_s": time.perf_counter() - row_t0,
                    "status": "ok",
                }
            )

    timing_rows.append(
        {
            "job_index": job_index,
            "experiment_id": f"{sensor_id}_{seed_id}_{stress_id}_{i_id}_bundle",
            "L": sensor_id,
            "I": i_id,
            "seed_id": seed_id,
            "stress_id": stress_id,
            "wall_time_s": time.perf_counter() - started,
            "status": "bundle_complete",
        }
    )
    return {"job_index": job_index, "tracks": track_rows, "summaries": summary_rows, "timing": timing_rows}


def add_baseline_deltas(summary_rows: list[dict], baseline_by_p: dict[str, dict]) -> None:
    b0 = baseline_by_p.get("P0", {})
    b0_p95 = float(b0.get(METRIC, float("nan")))
    b0_p50 = float(b0.get("trackmedian_err3d_p50_mm", float("nan")))
    for row in summary_rows:
        p_id = str(row.get("P", ""))
        same = baseline_by_p.get(p_id, {})
        same_p95 = float(same.get(METRIC, float("nan")))
        same_p50 = float(same.get("trackmedian_err3d_p50_mm", float("nan")))
        row["sameP_uwb_p50_mm"] = same_p50
        row["sameP_uwb_p95_mm"] = same_p95
        row["sameP_delta_p50_mm"] = same_p50 - float(row.get("trackmedian_err3d_p50_mm", float("nan")))
        row["sameP_delta_p95_mm"] = same_p95 - float(row.get(METRIC, float("nan")))
        row["b0_uwb_p50_mm"] = b0_p50
        row["b0_uwb_p95_mm"] = b0_p95
        row["b0_delta_p50_mm"] = b0_p50 - float(row.get("trackmedian_err3d_p50_mm", float("nan")))
        row["b0_delta_p95_mm"] = b0_p95 - float(row.get(METRIC, float("nan")))


def aggregate(summary_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    fusion = summary_df[summary_df["kind"] == "position_fusion"].copy()
    if fusion.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    by_stress = (
        fusion.groupby(["stress_id", "L", "P", "I", "T"], dropna=False)
        .agg(
            n=("experiment_id", "count"),
            p50_mean=("trackmedian_err3d_p50_mm", "mean"),
            p95_mean=(METRIC, "mean"),
            p95_std=(METRIC, "std"),
            rmse_mean=("trackmedian_err3d_rmse_mm", "mean"),
            horiz_xz_p95_mean=("trackmedian_horizontal_xz_p95_mm", "mean"),
            vertical_y_p95_mean=("trackmedian_vertical_y_p95_mm", "mean"),
            sameP_delta_p95_mean=("sameP_delta_p95_mm", "mean"),
            sameP_improved_fraction=("sameP_delta_p95_mm", lambda s: float((s > 0).mean())),
            b0_delta_p95_mean=("b0_delta_p95_mm", "mean"),
            radius_abs_mean=("trackmedian_radius_error_abs_mm", "mean"),
            thickness_p95_mean=("trackmedian_circle_thickness_p95_mm", "mean"),
            deltaR_rms_mean=("legacy_deltaR_error_rms_mm", "mean"),
        )
        .reset_index()
    )
    by_stress["sensor_label"] = by_stress["L"].map(SENSOR_LABEL).fillna(by_stress["L"])
    by_stress["experiment_short"] = (
        "X_A0_U4_"
        + by_stress["P"].astype(str)
        + "_"
        + by_stress["L"].astype(str)
        + "_"
        + by_stress["I"].astype(str)
        + "_"
        + by_stress["T"].astype(str)
    )
    by_stress["stress_rank"] = by_stress.groupby("stress_id")["p95_mean"].rank(method="first", ascending=True).astype(int)

    robust = (
        by_stress.groupby(["L", "P", "I", "T"], dropna=False)
        .agg(
            stress_count=("stress_id", "count"),
            p50_mean=("p50_mean", "mean"),
            p95_mean=("p95_mean", "mean"),
            p95_worst=("p95_mean", "max"),
            p95_std_across_stress=("p95_mean", "std"),
            rmse_mean=("rmse_mean", "mean"),
            horiz_xz_p95_mean=("horiz_xz_p95_mean", "mean"),
            vertical_y_p95_mean=("vertical_y_p95_mean", "mean"),
            sameP_delta_p95_mean=("sameP_delta_p95_mean", "mean"),
            sameP_improved_fraction=("sameP_improved_fraction", "mean"),
            b0_delta_p95_mean=("b0_delta_p95_mean", "mean"),
            radius_abs_mean=("radius_abs_mean", "mean"),
            thickness_p95_mean=("thickness_p95_mean", "mean"),
            deltaR_rms_mean=("deltaR_rms_mean", "mean"),
        )
        .reset_index()
    )
    robust["sensor_label"] = robust["L"].map(SENSOR_LABEL).fillna(robust["L"])
    robust["experiment_short"] = (
        "X_A0_U4_"
        + robust["P"].astype(str)
        + "_"
        + robust["L"].astype(str)
        + "_"
        + robust["I"].astype(str)
        + "_"
        + robust["T"].astype(str)
    )
    robust["robust_score"] = (
        robust["p95_mean"]
        + 0.35 * robust["p95_worst"]
        + 0.15 * robust["thickness_p95_mean"]
        - 0.25 * robust["sameP_delta_p95_mean"]
    )
    robust = robust.sort_values(["robust_score", "p95_worst", "p95_mean"]).reset_index(drop=True)
    robust["robust_rank"] = np.arange(1, len(robust) + 1)

    best_by_sensor_stress = (
        by_stress.sort_values(["stress_id", "L", "p95_mean"])
        .groupby(["stress_id", "L"], as_index=False)
        .head(1)
        .sort_values(["stress_id", "p95_mean"])
        .reset_index(drop=True)
    )
    return by_stress, robust, best_by_sensor_stress


def plot_outputs(out_dir: Path, by_stress: pd.DataFrame, robust: pd.DataFrame, best: pd.DataFrame) -> None:
    fig_dir = out_dir / "figs"
    fig_dir.mkdir(parents=True, exist_ok=True)
    if not best.empty:
        pivot = best.pivot(index="stress_id", columns="L", values="p95_mean").reindex(list(STRESS_CASES))
        ax = pivot.plot(kind="bar", figsize=(12, 6), width=0.82)
        ax.set_ylabel("Best candidate 3D P95 vs Opti (mm)")
        ax.set_xlabel("Stress case")
        ax.set_title("Best candidate per sensor under each stress")
        ax.grid(axis="y", alpha=0.25)
        ax.legend(title="IMU")
        plt.xticks(rotation=25, ha="right")
        plt.tight_layout()
        plt.savefig(fig_dir / "01_best_per_sensor_by_stress_p95.png", dpi=180)
        plt.close()

    if not robust.empty:
        top = robust.head(12).iloc[::-1]
        colors = top["L"].map({"L2": "#6aaed6", "L16": "#a070d6", "L20": "#23a37a"}).fillna("#888888")
        plt.figure(figsize=(12, 7))
        plt.barh(top["experiment_short"], top["p95_worst"], color=colors)
        plt.xlabel("Worst stress 3D P95 vs Opti (mm)")
        plt.title("Top robust candidates by worst-case P95")
        plt.grid(axis="x", alpha=0.25)
        plt.tight_layout()
        plt.savefig(fig_dir / "02_top12_worstcase_p95.png", dpi=180)
        plt.close()

    if not by_stress.empty:
        for sensor in sorted(by_stress["L"].dropna().unique()):
            sub = by_stress[by_stress["L"] == sensor].copy()
            best_rows = sub.sort_values(["stress_id", "p95_mean"]).groupby("stress_id", as_index=False).head(8)
            labels = best_rows["stress_id"] + "\n" + best_rows["P"] + "/" + best_rows["I"] + "/" + best_rows["T"]
            plt.figure(figsize=(14, 6))
            plt.bar(np.arange(len(best_rows)), best_rows["p95_mean"], color="#8d62c6")
            plt.xticks(np.arange(len(best_rows)), labels, rotation=55, ha="right", fontsize=8)
            plt.ylabel("3D P95 vs Opti (mm)")
            plt.title(f"{sensor} stress top candidates")
            plt.grid(axis="y", alpha=0.25)
            plt.tight_layout()
            plt.savefig(fig_dir / f"03_{sensor}_stress_top_candidates.png", dpi=180)
            plt.close()


def run(args: argparse.Namespace) -> dict:
    run_id = args.run_id or f"phase4_L2_L16_L20_stress_candidates_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir = OUT_BASE / run_id
    for d in [out_dir / "logs", out_dir / "tables", out_dir / "reports", out_dir / "figs"]:
        d.mkdir(parents=True, exist_ok=True)

    sensors = split_arg(args.sensors, None)
    seeds = split_arg(args.seeds, None)
    p_ids = split_arg(args.p_ids, ["P0", "P1", "P2", "P3", "P4", "P5"])
    i_ids = split_arg(args.i_ids, [f"I{i}" for i in range(9)])
    t_ids = split_arg(args.t_ids, ["T2", "T3", "T4", "T5"])
    stress_ids = [v.strip() for v in args.stress_cases.split(",") if v.strip()]
    bad_stress = [v for v in stress_ids if v not in STRESS_CASES]
    if bad_stress:
        raise ValueError(f"unsupported stress cases {bad_stress}; allowed={list(STRESS_CASES)}")
    for seed in seeds:
        if not re.match(r"^S\d{2}$", seed):
            raise ValueError(f"bad seed id {seed!r}; expected S00")

    workers = max(1, min(int(args.workers), os.cpu_count() or 1))
    start = time.perf_counter()
    sensor_meta = load_sensor_meta(sensors)
    print(f"[phase4-stress] run_id={run_id}", flush=True)
    print(f"[phase4-stress] sensors={sensors} seeds={seeds} stress={stress_ids} P={p_ids} I={i_ids} T={t_ids} workers={workers}", flush=True)

    write_json(
        out_dir / "manifest.json",
        {
            "run_id": run_id,
            "phase_status": "running",
            "phase": "phase4_lx_stress_candidates",
            "created_utc": datetime.now(UTC).isoformat(),
            "sensors": sensors,
            "seeds": seeds,
            "stress_cases": {sid: STRESS_CASES[sid] for sid in stress_ids},
            "P_ids": p_ids,
            "I_ids": i_ids,
            "T_ids": t_ids,
            "workers": workers,
            "host": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
            "git": git_status(),
        },
    )

    streams = F.load_streams()
    baseline_tracks, baseline_summaries, baseline_by_p = build_baselines(streams, p_ids)
    jobs: list[tuple[int, str, str, str, str]] = []
    for sensor_id in sensors:
        for seed_id in seeds:
            for stress_id in stress_ids:
                for i_id in i_ids:
                    jobs.append((len(jobs), sensor_id, seed_id, stress_id, i_id))

    write_csv(
        out_dir / "tables" / "stress_case_definitions.csv",
        [{"stress_id": sid, **STRESS_CASES[sid]} for sid in stress_ids],
    )
    write_csv(
        out_dir / "tables" / "stress_manifest.csv",
        [
            {"job_index": j[0], "L": j[1], "seed_id": j[2], "stress_id": j[3], "I": j[4], "P_ids": "+".join(p_ids), "T_ids": "+".join(t_ids)}
            for j in jobs
        ],
    )

    summary_rows: list[dict] = []
    track_rows: list[dict] = []
    timing_rows: list[dict] = []
    ctx = mp.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=init_worker, initargs=(streams, sensor_meta, p_ids, t_ids)) as pool:
        futures = [pool.submit(worker, job) for job in jobs]
        for done_count, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            summary_rows.extend(result["summaries"])
            track_rows.extend(result["tracks"])
            timing_rows.extend(result["timing"])
            if done_count == 1 or done_count % 10 == 0 or done_count == len(jobs):
                elapsed = time.perf_counter() - start
                rate = done_count / elapsed if elapsed > 0 else float("nan")
                eta = (len(jobs) - done_count) / rate if rate > 0 else float("nan")
                print(f"[phase4-stress] bundles {done_count}/{len(jobs)} done, rate={fmt(rate, 3)} bundles/s, eta={fmt(eta / 60.0, 1)} min", flush=True)
                write_csv(out_dir / "tables" / "stress_summary_partial.csv", summary_rows)
                write_csv(out_dir / "tables" / "stress_timing_partial.csv", timing_rows)

    add_baseline_deltas(summary_rows, baseline_by_p)
    summary_df = pd.DataFrame(summary_rows)
    track_df = pd.DataFrame(track_rows)
    baseline_df = pd.DataFrame(baseline_summaries)
    by_stress, robust, best = aggregate(summary_df)
    elapsed = time.perf_counter() - start

    write_csv(out_dir / "tables" / "stress_uwb_baselines.csv", baseline_summaries)
    write_csv(out_dir / "tables" / "stress_baseline_track_metrics.csv", baseline_tracks)
    write_csv(out_dir / "tables" / "stress_summary.csv", summary_rows)
    write_csv(out_dir / "tables" / "stress_track_metrics.csv", track_rows)
    write_csv(out_dir / "tables" / "stress_timing.csv", timing_rows)
    by_stress.to_csv(out_dir / "tables" / "stress_by_case_ranking.csv", index=False)
    robust.to_csv(out_dir / "tables" / "stress_robust_ranking.csv", index=False)
    best.to_csv(out_dir / "tables" / "stress_best_by_sensor_case.csv", index=False)

    plot_outputs(out_dir, by_stress, robust, best)

    top_cols = [
        "robust_rank",
        "experiment_short",
        "sensor_label",
        "p95_mean",
        "p95_worst",
        "sameP_delta_p95_mean",
        "sameP_improved_fraction",
        "horiz_xz_p95_mean",
        "vertical_y_p95_mean",
        "radius_abs_mean",
        "thickness_p95_mean",
    ]
    case_cols = [
        "stress_id",
        "experiment_short",
        "sensor_label",
        "p95_mean",
        "sameP_delta_p95_mean",
        "horiz_xz_p95_mean",
        "vertical_y_p95_mean",
        "radius_abs_mean",
        "thickness_p95_mean",
    ]
    report = [
        "# Phase 4 L2/L16/L20 Stress Candidate Test",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Run ID: `{run_id}`",
        f"Status: `complete`",
        f"Wall time: {fmt(elapsed / 60.0, 1)} min",
        "",
        "## Scope",
        "",
        f"- Sensors: `{', '.join(sensors)}`",
        f"- Seeds: `{', '.join(seeds)}`",
        f"- Stress cases: `{', '.join(stress_ids)}`",
        f"- Position branch: `A0/U4`, P=`{', '.join(p_ids)}`, I=`{', '.join(i_ids)}`, T=`{', '.join(t_ids)}`",
        "- Evaluation truth: Opti/Vicon. Same-P deltas compare each fusion row against pure UWB with the same P filter.",
        "- Coordinate source: Phase 4 consumes the official aligned ROTO table with columns `uwb_x_mm`, `uwb_y_vertical_mm`, `uwb_z_mm`, `opti_x_mm`, `opti_y_vertical_mm`, `opti_z_mm`.",
        "- Metric naming follows that table: `horizontal_xz` is the aligned horizontal plane and `vertical_y` is the aligned vertical axis. Do not read this as raw device XY/Z naming.",
        "",
        "## Stress Cases",
        "",
        md_table(pd.DataFrame([{"stress_id": sid, **STRESS_CASES[sid]} for sid in stress_ids]), ["stress_id", "description", "bias", "noise", "rw", "vib", "extrinsic"], max_rows=20),
        "",
        "## Robust Ranking",
        "",
        md_table(robust, top_cols, max_rows=20),
        "",
        "## Best Row Per Sensor And Stress",
        "",
        md_table(best, case_cols, max_rows=30),
        "",
        "## Figures",
        "",
        "- `figs/01_best_per_sensor_by_stress_p95.png`",
        "- `figs/02_top12_worstcase_p95.png`",
        "- `figs/03_L2_stress_top_candidates.png`",
        "- `figs/03_L16_stress_top_candidates.png`",
        "- `figs/03_L20_stress_top_candidates.png`",
        "",
        "## Tables",
        "",
        "- `tables/stress_robust_ranking.csv`",
        "- `tables/stress_by_case_ranking.csv`",
        "- `tables/stress_best_by_sensor_case.csv`",
        "- `tables/stress_summary.csv`",
        "- `tables/stress_track_metrics.csv`",
    ]
    (out_dir / "reports" / "PHASE4_L2_L16_L20_STRESS_CANDIDATES.md").write_text("\n".join(report), encoding="utf-8")
    write_json(
        out_dir / "manifest.json",
        {
            "run_id": run_id,
            "phase_status": "complete",
            "phase": "phase4_lx_stress_candidates",
            "created_utc": datetime.now(UTC).isoformat(),
            "elapsed_s": elapsed,
            "sensors": sensors,
            "seeds": seeds,
            "stress_cases": {sid: STRESS_CASES[sid] for sid in stress_ids},
            "P_ids": p_ids,
            "I_ids": i_ids,
            "T_ids": t_ids,
            "bundle_jobs": len(jobs),
            "fusion_rows": int((summary_df["kind"] == "position_fusion").sum()) if not summary_df.empty else 0,
            "imu_rows": int((summary_df["kind"] == "imu_only").sum()) if not summary_df.empty else 0,
            "baseline_rows": len(baseline_summaries),
            "workers": workers,
            "sensor_metadata": sensor_meta,
            "outputs": {
                "report": "reports/PHASE4_L2_L16_L20_STRESS_CANDIDATES.md",
                "robust_ranking": "tables/stress_robust_ranking.csv",
                "case_ranking": "tables/stress_by_case_ranking.csv",
                "best_by_sensor_case": "tables/stress_best_by_sensor_case.csv",
                "summary": "tables/stress_summary.csv",
                "track_metrics": "tables/stress_track_metrics.csv",
            },
            "host": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
            "git": git_status(),
        },
    )
    return {"run_id": run_id, "run_dir": str(out_dir), "elapsed_s": elapsed, "fusion_rows": int((summary_df["kind"] == "position_fusion").sum())}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run L2/L16/L20 stress candidate tests.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--sensors", default=",".join(DEFAULT_SENSORS))
    parser.add_argument("--seeds", default=",".join(DEFAULT_SEEDS))
    parser.add_argument("--stress-cases", default=",".join(STRESS_CASES))
    parser.add_argument("--p-ids", default=",".join(DEFAULT_P_IDS))
    parser.add_argument("--i-ids", default=",".join(DEFAULT_I_IDS))
    parser.add_argument("--t-ids", default=",".join(DEFAULT_T_IDS))
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
