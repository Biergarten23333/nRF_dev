#!/usr/bin/env python3
"""Official Phase 2 runner.

The first stage of Phase 2 fulfills the gates that were intentionally limited
in Phase 1:

* G5: repeated stochastic IMU seeds for screening.
* G3: raw-range residual/bias policy table for tight-fusion rows.

This is not a pre-phase.  Outputs live under runs/phase2_screening/<run_id>/.
If gates fail, the Phase 2 run is marked blocked_before_screening inside that
same directory.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import math
import os
import platform
import subprocess
import sys
import time
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
N_SCREENING_SEEDS = 5


def load_phase1_module():
    spec = importlib.util.spec_from_file_location("phase1_vertical_slice", PHASE1_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {PHASE1_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


P1 = load_phase1_module()


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


def mad_sigma(values) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    med = np.median(arr)
    return float(1.4826 * np.median(np.abs(arr - med)))


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


def latest_phase1_run() -> str:
    root = SIM_ROOT / "runs" / "phase1_vertical_slice"
    runs = [p.name for p in root.iterdir() if (p / "tables" / "phase1_summary.csv").exists()]
    if not runs:
        raise FileNotFoundError("no phase1_vertical_slice run with phase1_summary.csv")
    return sorted(runs)[-1]


def load_a0_layout() -> tuple[np.ndarray, np.ndarray, float, list[str]]:
    layout_path = OFFICIAL_ROOT / "solver" / "outputs" / "v1_to_v4_io_field_check_US" / "v4-io" / "layout.json"
    data = json.loads(layout_path.read_text(encoding="utf-8"))
    anchors = sorted(data["anchors"], key=lambda row: int(row["id"]))
    xyz = np.asarray([[float(a["x_mm"]), float(a["y_mm"]), float(a["z_mm"])] for a in anchors], dtype=float)
    delays = np.asarray([float(a.get("d_anchor_mm", 0.0)) for a in anchors], dtype=float)
    labels = [str(a.get("label", a["id"])) for a in anchors]
    return xyz, delays, float(data.get("tag_delay_mm", 0.0)), labels


def interpolate_xyz(time_s: np.ndarray, xyz: np.ndarray, query_s: np.ndarray) -> np.ndarray:
    t = np.asarray(time_s, dtype=float)
    pts = np.asarray(xyz, dtype=float)
    q = np.asarray(query_s, dtype=float)
    good = np.isfinite(t) & np.isfinite(pts).all(axis=1)
    out = np.full((q.size, 3), np.nan, dtype=float)
    if int(np.sum(good)) < 2:
        return out
    order = np.argsort(t[good])
    tg = t[good][order]
    pg = pts[good][order]
    for axis in range(3):
        out[:, axis] = np.interp(q, tg, pg[:, axis], left=np.nan, right=np.nan)
    return out


def load_b0_samples() -> pd.DataFrame:
    case = P1.BASELINES[0]
    df = P1.load_official_samples(case.sample_path)
    return P1.official_to_samples(df, case.experiment_id, case.deployability, case.description)


def run_multiseed_l2_drift(b0_samples: pd.DataFrame, run_id: str, out_dir: Path) -> tuple[list[dict], list[dict]]:
    sensor = P1.SENSOR_PHASE1["L2"]
    seed_rows: list[dict] = []
    track_rows: list[dict] = []
    for seed_index in range(N_SCREENING_SEEDS):
        for (capture_id, tag), g0 in b0_samples.groupby(["capture_id", "tag"], sort=True):
            g = g0.sort_values("time_s").copy()
            truth = g[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float)
            times = g["time_s"].to_numpy(float)
            seed = stable_seed(run_id, "G5", "L2", "I3", seed_index, capture_id, tag)
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
            endpoint = drift[-1]
            drift_norm = np.linalg.norm(drift, axis=1)
            duration = float(times[-1] - times[0]) if len(times) > 1 else float("nan")
            endpoint_3d = float(np.linalg.norm(endpoint))
            row = {
                "experiment_id": "X_A0_L2_I3_T11",
                "stage": "stage0_gate_fulfillment",
                "gate_id": "G5_noise_seed_repeats",
                "seed_index": seed_index,
                "seed": int(seed),
                "capture_id": str(capture_id),
                "tag": str(tag),
                "endpoint_drift_3d_mm": endpoint_3d,
                "endpoint_drift_xz_mm": float(math.sqrt(endpoint[0] * endpoint[0] + endpoint[2] * endpoint[2])),
                "endpoint_drift_y_mm": float(abs(endpoint[1])),
                "drift_rate_3d_mm_s": endpoint_3d / duration if math.isfinite(duration) and duration > 0 else float("nan"),
                "drift_p50_mm": pct(drift_norm, 50),
                "drift_p95_mm": pct(drift_norm, 95),
            }
            seed_rows.append(
                {
                    "seed_index": seed_index,
                    "seed": int(seed),
                    "capture_id": str(capture_id),
                    "tag": str(tag),
                    "L": "L2",
                    "I": "I3",
                    "source": "stable sha256(phase2_run_id, G5, L2, I3, seed_index, capture_id, tag)",
                }
            )
            track_rows.append(row)
    write_csv(out_dir / "tables" / "g5_l2_i3_multiseed_drift_tracks.csv", track_rows)
    write_json(out_dir / "manifests" / f"noise_seeds_{run_id}.json", seed_rows)
    return track_rows, seed_rows


def run_range_bias_policy(b0_samples: pd.DataFrame, run_id: str, out_dir: Path) -> tuple[list[dict], list[dict]]:
    pairing_rows = P1.build_pairing_manifest()
    anchor_xyz, anchor_delay, tag_delay, labels = load_a0_layout()
    sample_by_track = {k: g.sort_values("opti_time_s").copy() for k, g in b0_samples.groupby(["capture_id", "tag"], sort=True)}
    beta_by_capture = {str(r["capture_id"]): float(r["beta_s"]) for r in pairing_rows if r.get("pairing_ok")}
    residual_rows: list[dict] = []
    track_rows: list[dict] = []
    for pair in pairing_rows:
        if not pair.get("pairing_ok"):
            continue
        cap_id = str(pair["capture_id"])
        cap_dir = OFFICIAL_ROOT / str(pair["uwb_capture_path"])
        tr_matches = sorted(cap_dir.glob("tag_capture*/tr_all.csv"))
        if not tr_matches:
            continue
        tr_path = tr_matches[0]
        usecols = ["host_elapsed_s", "sweep", "peer_name", "anchor_id", "range_mm", "quality_percent", "valid"]
        raw = pd.read_csv(tr_path, usecols=usecols)
        raw = raw[(raw["valid"].astype(float) > 0) & (raw["range_mm"].astype(float) > 0)].copy()
        raw["anchor_id"] = raw["anchor_id"].astype(int)
        raw = raw[raw["anchor_id"].between(0, len(labels) - 1)].copy()
        beta = beta_by_capture[cap_id]
        for tag in TAGS:
            g = raw[raw["peer_name"].astype(str) == tag].copy()
            if g.empty:
                continue
            samples = sample_by_track[(cap_id, tag)]
            opti_t = g["host_elapsed_s"].to_numpy(float) + beta
            opti_xyz = interpolate_xyz(
                samples["opti_time_s"].to_numpy(float),
                samples[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float),
                opti_t,
            )
            aid = g["anchor_id"].to_numpy(int)
            dist = np.linalg.norm(opti_xyz - anchor_xyz[aid], axis=1)
            expected = dist + anchor_delay[aid] + tag_delay
            residual = g["range_mm"].to_numpy(float) - expected
            good = np.isfinite(residual)
            g = g.iloc[np.where(good)[0]].copy()
            residual = residual[good]
            aid = aid[good]
            for a in range(len(labels)):
                vals = residual[aid == a]
                if vals.size == 0:
                    continue
                row = {
                    "capture_id": cap_id,
                    "tag": tag,
                    "anchor_id": a,
                    "anchor_label": labels[a],
                    "n_samples": int(vals.size),
                    "range_bias_median_mm": pct(vals, 50),
                    "range_residual_p05_mm": pct(vals, 5),
                    "range_residual_p95_mm": pct(vals, 95),
                    "range_residual_rmse_mm": rms(vals),
                    "range_sigma_mad_mm": mad_sigma(vals),
                    "policy": "subtract median residual per anchor/tag/capture for R2; use MAD sigma and quality/outlier downweighting for R4",
                }
                residual_rows.append(row)
            counts = g.groupby(["host_elapsed_s", "sweep"])["anchor_id"].nunique().to_numpy(float)
            track_rows.append(
                {
                    "capture_id": cap_id,
                    "tag": tag,
                    "raw_range_path": str(tr_path.relative_to(OFFICIAL_ROOT)),
                    "frames": int(counts.size),
                    "ge4_ratio": float(np.mean(counts >= 4)) if counts.size else 0.0,
                    "full8_ratio": float(np.mean(counts >= 8)) if counts.size else 0.0,
                    "valid_anchor_count_median": pct(counts, 50),
                    "bias_rows": int(sum(1 for r in residual_rows if r["capture_id"] == cap_id and r["tag"] == tag)),
                }
            )
    write_csv(out_dir / "tables" / "g3_range_bias_by_capture_tag_anchor.csv", residual_rows)
    write_csv(out_dir / "tables" / "g3_raw_range_availability_tracks.csv", track_rows)

    # Screening policy table: aggregate across captures/tags for each anchor.
    policy_rows: list[dict] = []
    df = pd.DataFrame(residual_rows)
    if not df.empty:
        for aid, ga in df.groupby("anchor_id", sort=True):
            weights = ga["n_samples"].astype(float).to_numpy()
            bias = ga["range_bias_median_mm"].astype(float).to_numpy()
            sigma = ga["range_sigma_mad_mm"].astype(float).to_numpy()
            policy_rows.append(
                {
                    "R": "R2",
                    "anchor_id": int(aid),
                    "anchor_label": str(ga["anchor_label"].iloc[0]),
                    "range_bias_mm": float(np.average(bias, weights=weights)),
                    "range_sigma_mm": float(np.average(sigma, weights=weights)),
                    "n_capture_tag_anchor_rows": int(len(ga)),
                    "n_raw_samples": int(np.sum(weights)),
                    "missing_link_policy": "missing measurements omitted from residual stack; require >=4 anchors for position update",
                    "quality_policy": "R2 uses median bias; R4 additionally downweights low quality and large robust residuals",
                }
            )
    write_csv(out_dir / "tables" / "g3_range_bias_policy_R2.csv", policy_rows)
    return residual_rows, track_rows


def summarize_g5(track_rows: list[dict]) -> dict:
    df = pd.DataFrame(track_rows)
    endpoint = df["endpoint_drift_3d_mm"].to_numpy(float)
    return {
        "gate_id": "G5_noise_seed_repeats",
        "n_seeds": int(df["seed_index"].nunique()) if not df.empty else 0,
        "n_tracks": int(len(df)),
        "endpoint_drift_3d_p50_mm": pct(endpoint, 50),
        "endpoint_drift_3d_p05_mm": pct(endpoint, 5),
        "endpoint_drift_3d_p95_mm": pct(endpoint, 95),
        "pass": bool((not df.empty) and df["seed_index"].nunique() >= N_SCREENING_SEEDS and pct(endpoint, 5) > 250.0),
    }


def summarize_g3(residual_rows: list[dict], availability_rows: list[dict]) -> dict:
    rdf = pd.DataFrame(residual_rows)
    adf = pd.DataFrame(availability_rows)
    anchors_ok = 0 if rdf.empty else int(rdf.groupby(["capture_id", "tag"])["anchor_id"].nunique().min())
    ge4_min = 0.0 if adf.empty else float(adf["ge4_ratio"].min())
    pass_gate = bool((not rdf.empty) and (not adf.empty) and len(adf) == 34 and anchors_ok >= 8 and ge4_min >= 0.95)
    return {
        "gate_id": "G3_range_bias_policy",
        "n_capture_tag_tracks": int(len(adf)),
        "min_anchor_bias_rows_per_track": anchors_ok,
        "min_ge4_ratio": ge4_min,
        "n_bias_rows": int(len(rdf)),
        "pass": pass_gate,
    }


def validation_rows(g3: dict, g5: dict) -> list[dict]:
    return [
        {
            "gate_id": "G3_range_bias_policy",
            "status": "PASS" if g3["pass"] else "FAIL",
            "phase": "phase2",
            "stage": "stage0_gate_fulfillment",
            "evidence": (
                f"bias_rows={g3['n_bias_rows']}; tracks={g3['n_capture_tag_tracks']}; "
                f"min_anchor_rows={g3['min_anchor_bias_rows_per_track']}; min_ge4_ratio={fmt(g3['min_ge4_ratio'], 3)}"
            ),
            "blocking_screening": not bool(g3["pass"]),
        },
        {
            "gate_id": "G5_noise_seed_repeats",
            "status": "PASS_SCREENING" if g5["pass"] else "FAIL",
            "phase": "phase2",
            "stage": "stage0_gate_fulfillment",
            "evidence": (
                f"seeds={g5['n_seeds']}; tracks={g5['n_tracks']}; "
                f"endpoint drift p05/p50/p95={fmt(g5['endpoint_drift_3d_p05_mm'])}/"
                f"{fmt(g5['endpoint_drift_3d_p50_mm'])}/{fmt(g5['endpoint_drift_3d_p95_mm'])} mm"
            ),
            "blocking_screening": not bool(g5["pass"]),
        },
    ]


def write_reports(run_dir: Path, gate_rows: list[dict], g3: dict, g5: dict, phase_status: str, phase1_run: str) -> None:
    reports = run_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    gates_md = [
        "# Phase 2 Validation Gates",
        "",
        f"Phase 2 run status: `{phase_status}`",
        f"Phase 1 source run: `{phase1_run}`",
        "",
        markdown_table(gate_rows, ["gate_id", "status", "blocking_screening", "evidence"]),
        "",
        "G3 creates the raw-range residual/bias policy table for tight-fusion rows.",
        "G5 creates repeated stochastic IMU drift evidence for screening.",
        "",
    ]
    (reports / "VALIDATION_GATES.md").write_text("\n".join(gates_md), encoding="utf-8")
    phase_md = [
        "# Phase 2 Screening",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Phase status: `{phase_status}`",
        "",
        "## Stage 0 Gate Fulfillment",
        "",
        markdown_table(gate_rows, ["gate_id", "status", "blocking_screening", "evidence"]),
        "",
        "Broad screening has not been run by this stage script yet.",
        "If `phase_status = ready_for_stage1_screening`, the next command may run stage1 inside the same Phase 2 directory.",
        "",
    ]
    (reports / "PHASE2_SCREENING.md").write_text("\n".join(phase_md), encoding="utf-8")


def run(args: argparse.Namespace) -> dict:
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    phase1_run = args.phase1_run or latest_phase1_run()
    run_dir = SIM_ROOT / "runs" / "phase2_screening" / run_id
    stage0 = run_dir / "stage0_gate_fulfillment"
    for path in [
        stage0 / "tables",
        stage0 / "manifests",
        run_dir / "tables",
        run_dir / "reports",
        run_dir / "logs",
        run_dir / "stage1_screening",
        run_dir / "stage2_ranking_and_visual_audit",
    ]:
        path.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    b0_samples = load_b0_samples()
    g5_tracks, seed_rows = run_multiseed_l2_drift(b0_samples, run_id, stage0)
    g3_residuals, g3_availability = run_range_bias_policy(b0_samples, run_id, stage0)
    g5 = summarize_g5(g5_tracks)
    g3 = summarize_g3(g3_residuals, g3_availability)
    gates = validation_rows(g3, g5)
    phase_status = "ready_for_stage1_screening" if g3["pass"] and g5["pass"] else "blocked_before_screening"

    write_csv(run_dir / "tables" / "validation_gates.csv", gates)
    write_csv(run_dir / "tables" / "phase2_stage0_summary.csv", [g3, g5])
    write_csv(run_dir / "tables" / "phase2_summary.csv", [])
    write_csv(run_dir / "tables" / "phase2_ranked_top50.csv", [])
    write_reports(run_dir, gates, g3, g5, phase_status, phase1_run)

    manifest = {
        "run_id": run_id,
        "phase": "phase2_screening",
        "phase_status": phase_status,
        "stage_completed": "stage0_gate_fulfillment",
        "phase1_source_run": phase1_run,
        "generated_utc": datetime.now(UTC).isoformat(),
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "command": " ".join([os.path.basename(__file__)] + os.sys.argv[1:]),
        "git": git_status(),
        "alignment_policy": "fixed_capture_level_beta_from_primary_v4io_U4",
        "capture_set": "R01-R17",
        "elapsed_s": time.perf_counter() - start,
        "outputs": {
            "validation_gates": str((run_dir / "tables" / "validation_gates.csv").relative_to(SIM_ROOT)),
            "range_bias_policy": str((stage0 / "tables" / "g3_range_bias_policy_R2.csv").relative_to(SIM_ROOT)),
            "noise_seeds": str((stage0 / "manifests" / f"noise_seeds_{run_id}.json").relative_to(SIM_ROOT)),
        },
    }
    write_json(run_dir / "manifest.json", manifest)
    write_json(SIM_ROOT / "manifests" / f"phase2_{run_id}.json", manifest)
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "phase_status": phase_status,
        "gates": gates,
        "g3": g3,
        "g5": g5,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run official Phase 2 gate fulfillment and optional screening.")
    parser.add_argument("--run-id", default="", help="Optional Phase 2 run ID.")
    parser.add_argument("--phase1-run", default="", help="Phase 1 run ID to reference. Defaults to latest.")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({"run_id": result["run_id"], "run_dir": result["run_dir"], "phase_status": result["phase_status"]}, indent=2))
    print("\nPHASE 2 GATES")
    for row in result["gates"]:
        print(f"{row['gate_id']}: {row['status']} | blocking_screening={row['blocking_screening']} | {row['evidence']}")


if __name__ == "__main__":
    main()
