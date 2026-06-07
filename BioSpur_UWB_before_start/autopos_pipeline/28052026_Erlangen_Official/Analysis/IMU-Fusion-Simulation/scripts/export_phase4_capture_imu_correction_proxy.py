#!/usr/bin/env python3
"""Export capture-level IMU correction proxy streams for a selected Phase 4 row.

The simulator emits raw/proxy IMU measurement channels, IMU-prior position,
fused position, finite-difference fused acceleration, and correction metrics.
Gyro output is still a simulated proxy because this dataset path does not carry
full orientation truth/body-frame angular velocity.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
FACTORY_SCRIPT = SIM_ROOT / "scripts" / "run_phase4_l2_singleI_full_factory.py"
OUT_BASE = SIM_ROOT / "runs" / "phase4_analysis"


def load_factory():
    spec = importlib.util.spec_from_file_location("phase4_factory_export", FACTORY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {FACTORY_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def deriv(time_s: np.ndarray, xyz_mm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = np.asarray(time_s, dtype=float)
    xyz = np.asarray(xyz_mm, dtype=float)
    vel = np.full_like(xyz, np.nan, dtype=float)
    acc = np.full_like(xyz, np.nan, dtype=float)
    good = np.isfinite(t) & np.isfinite(xyz).all(axis=1)
    if int(good.sum()) < 3:
        return vel, acc
    idx = np.where(good)[0]
    tg = t[idx]
    if np.any(np.diff(tg) <= 0):
        return vel, acc
    vg = np.column_stack([np.gradient(xyz[idx, axis], tg, edge_order=1) for axis in range(3)])
    ag = np.column_stack([np.gradient(vg[:, axis], tg, edge_order=1) for axis in range(3)])
    vel[idx] = vg
    acc[idx] = ag
    return vel, acc


def add_kinematics(out: pd.DataFrame, prefix: str, cols: list[str]) -> None:
    vel_all = np.full((len(out), 3), np.nan, dtype=float)
    acc_all = np.full((len(out), 3), np.nan, dtype=float)
    for _, idx in out.groupby(["capture_id", "tag"], sort=False).groups.items():
        loc = np.asarray(list(idx), dtype=int)
        g = out.iloc[loc]
        vel, acc = deriv(g["time_s"].to_numpy(float), g[cols].to_numpy(float))
        vel_all[loc] = vel
        acc_all[loc] = acc
    for axis, name in enumerate(["x", "y", "z"]):
        out[f"{prefix}_vel_{name}_mm_s"] = vel_all[:, axis]
        out[f"{prefix}_acc_{name}_mm_s2"] = acc_all[:, axis]
    out[f"{prefix}_speed_mm_s"] = np.linalg.norm(vel_all, axis=1)
    out[f"{prefix}_acc_norm_mm_s2"] = np.linalg.norm(acc_all, axis=1)


def build_seed_export(F, sensor: str, imu_filter: str, p_id: str, t_id: str, seed: str) -> pd.DataFrame:
    F._SEED_ID = seed
    F._SENSOR_ID = sensor
    l_props, _sensor_meta = F.install_sensor_props(sensor)
    streams = F.load_streams()
    stream = streams[("A0", "U4", p_id)]
    base = streams[("A0", "U4", "P0")]
    run_id = f"phase4_{sensor.lower()}_full_{seed}_A0"
    imu_prior = F.S1.simulate_imu_for_li(base, run_id, sensor, imu_filter)
    params = F.POSITION_T_PARAMS[t_id]
    process = F.S1.li_process_factor(sensor, imu_filter)
    fused = F.S1.position_fusion_samples(
        stream,
        imu_prior,
        f"X_A0_U4_{p_id}_{sensor}_{imu_filter}_{t_id}",
        str(params["deployability"]),
        f"Phase4 export {sensor}/{imu_filter}/{p_id}/{t_id}",
        float(params["prior_sigma_base"]) * process,
        float(params["measurement_sigma"]),
    )

    keys = ["capture_id", "tag", "time_s"]
    prior_cols = keys + ["x_mm", "y_mm", "z_mm", "err3d_mm", "imu_only_endpoint_drift_3d_mm", "imu_only_drift_rate_3d_mm_s"]
    prior = imu_prior[prior_cols].rename(
        columns={
            "x_mm": "imu_prior_x_mm",
            "y_mm": "imu_prior_y_mm",
            "z_mm": "imu_prior_z_mm",
            "err3d_mm": "imu_prior_err3d_mm",
        }
    )
    out = fused.merge(prior, on=keys, how="left")
    out.insert(0, "seed_id", seed)
    out["L"] = sensor
    out["I"] = imu_filter
    out["P"] = p_id
    out["T"] = t_id
    out["solver_row"] = f"X_A0_U4_{p_id}_{sensor}_{imu_filter}_{t_id}"
    out = out.rename(columns={"x_mm": "fused_x_mm", "y_mm": "fused_y_mm", "z_mm": "fused_z_mm", "err3d_mm": "fused_err3d_mm"})

    for axis in ["x", "y", "z"]:
        out[f"correction_from_imu_prior_{axis}_mm"] = out[f"fused_{axis}_mm"] - out[f"imu_prior_{axis}_mm"]
        out[f"correction_from_uwb_{axis}_mm"] = out[f"fused_{axis}_mm"] - out[f"uwb_{axis}_mm"]
    out["correction_from_imu_prior_norm_mm"] = np.linalg.norm(out[[f"correction_from_imu_prior_{a}_mm" for a in ["x", "y", "z"]]].to_numpy(float), axis=1)
    out["correction_from_uwb_norm_mm"] = np.linalg.norm(out[[f"correction_from_uwb_{a}_mm" for a in ["x", "y", "z"]]].to_numpy(float), axis=1)

    add_kinematics(out, "fused", ["fused_x_mm", "fused_y_mm", "fused_z_mm"])
    add_kinematics(out, "imu_prior", ["imu_prior_x_mm", "imu_prior_y_mm", "imu_prior_z_mm"])
    add_kinematics(out, "opti", ["opti_x_mm", "opti_y_mm", "opti_z_mm"])

    keep = [
        "seed_id",
        "solver_row",
        "L",
        "I",
        "P",
        "T",
        "capture_id",
        "tag",
        "time_s",
        "opti_time_s",
        "fused_x_mm",
        "fused_y_mm",
        "fused_z_mm",
        "uwb_x_mm",
        "uwb_y_mm",
        "uwb_z_mm",
        "imu_prior_x_mm",
        "imu_prior_y_mm",
        "imu_prior_z_mm",
        "opti_x_mm",
        "opti_y_mm",
        "opti_z_mm",
        "fused_err3d_mm",
        "imu_prior_err3d_mm",
        "uwb_innovation_nis",
        "uwb_update_accept_rate",
        "prior_missing_samples",
        "correction_from_imu_prior_x_mm",
        "correction_from_imu_prior_y_mm",
        "correction_from_imu_prior_z_mm",
        "correction_from_imu_prior_norm_mm",
        "correction_from_uwb_x_mm",
        "correction_from_uwb_y_mm",
        "correction_from_uwb_z_mm",
        "correction_from_uwb_norm_mm",
        "fused_vel_x_mm_s",
        "fused_vel_y_mm_s",
        "fused_vel_z_mm_s",
        "fused_speed_mm_s",
        "fused_acc_x_mm_s2",
        "fused_acc_y_mm_s2",
        "fused_acc_z_mm_s2",
        "fused_acc_norm_mm_s2",
        "imu_prior_vel_x_mm_s",
        "imu_prior_vel_y_mm_s",
        "imu_prior_vel_z_mm_s",
        "imu_prior_speed_mm_s",
        "imu_prior_acc_x_mm_s2",
        "imu_prior_acc_y_mm_s2",
        "imu_prior_acc_z_mm_s2",
        "imu_prior_acc_norm_mm_s2",
        "opti_vel_x_mm_s",
        "opti_vel_y_mm_s",
        "opti_vel_z_mm_s",
        "opti_speed_mm_s",
        "opti_acc_x_mm_s2",
        "opti_acc_y_mm_s2",
        "opti_acc_z_mm_s2",
        "opti_acc_norm_mm_s2",
        "imu_only_endpoint_drift_3d_mm",
        "imu_only_drift_rate_3d_mm_s",
    ]
    keep.extend(c for c in out.columns if c.startswith("imu_") and c not in keep)
    return out[[c for c in keep if c in out.columns]]


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for seed, g in df.groupby("seed_id", sort=True):
        rows.append(
            {
                "seed_id": seed,
                "rows": len(g),
                "capture_tags": int(g[["capture_id", "tag"]].drop_duplicates().shape[0]),
                "fused_err3d_p50_mm": float(np.nanpercentile(g["fused_err3d_mm"], 50)),
                "fused_err3d_p95_mm": float(np.nanpercentile(g["fused_err3d_mm"], 95)),
                "imu_prior_err3d_p95_mm": float(np.nanpercentile(g["imu_prior_err3d_mm"], 95)),
                "correction_from_imu_prior_p50_mm": float(np.nanpercentile(g["correction_from_imu_prior_norm_mm"], 50)),
                "correction_from_imu_prior_p95_mm": float(np.nanpercentile(g["correction_from_imu_prior_norm_mm"], 95)),
                "fused_acc_norm_p95_mm_s2": float(np.nanpercentile(g["fused_acc_norm_mm_s2"], 95)),
                "opti_acc_norm_p95_mm_s2": float(np.nanpercentile(g["opti_acc_norm_mm_s2"], 95)),
                "imu_acc_correction_norm_p50_mm_s2": float(np.nanpercentile(g["imu_acc_correction_norm_proxy_mm_s2"], 50)) if "imu_acc_correction_norm_proxy_mm_s2" in g else float("nan"),
                "imu_acc_correction_norm_p95_mm_s2": float(np.nanpercentile(g["imu_acc_correction_norm_proxy_mm_s2"], 95)) if "imu_acc_correction_norm_proxy_mm_s2" in g else float("nan"),
                "imu_acc_correction_ratio_p50": float(np.nanpercentile(g["imu_acc_correction_ratio_proxy"], 50)) if "imu_acc_correction_ratio_proxy" in g else float("nan"),
                "imu_gyro_correction_norm_p50_dps": float(np.nanpercentile(g["imu_gyro_correction_norm_proxy_dps"], 50)) if "imu_gyro_correction_norm_proxy_dps" in g else float("nan"),
                "imu_gyro_correction_norm_p95_dps": float(np.nanpercentile(g["imu_gyro_correction_norm_proxy_dps"], 95)) if "imu_gyro_correction_norm_proxy_dps" in g else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Phase 4 capture-level IMU correction proxy streams.")
    parser.add_argument("--out-run", default="phase4_all_active_18L_5seed_5090D_20260607T095219Z")
    parser.add_argument("--sensor", default="L19")
    parser.add_argument("--imu-filter", default="I5")
    parser.add_argument("--p-id", default="P4")
    parser.add_argument("--t-id", default="T5")
    parser.add_argument("--seeds", default="S00,S01,S02,S03,S04")
    args = parser.parse_args()

    F = load_factory()
    seeds = [s.strip().upper() for s in args.seeds.split(",") if s.strip()]
    out_dir = OUT_BASE / args.out_run / "exports" / f"{args.sensor}_{args.imu_filter}_{args.p_id}_{args.t_id}_proxy_corrected_kinematics"
    out_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    for seed in seeds:
        df = build_seed_export(F, args.sensor.upper(), args.imu_filter.upper(), args.p_id.upper(), args.t_id.upper(), seed)
        df.to_csv(out_dir / f"proxy_corrected_kinematics_{args.sensor}_{args.imu_filter}_{args.p_id}_{args.t_id}_{seed}.csv", index=False)
        frames.append(df)
    all_df = pd.concat(frames, ignore_index=True)
    all_df.to_csv(out_dir / f"proxy_corrected_kinematics_{args.sensor}_{args.imu_filter}_{args.p_id}_{args.t_id}_ALL_SEEDS.csv", index=False)
    summary = summarize(all_df)
    summary.to_csv(out_dir / "proxy_corrected_kinematics_summary.csv", index=False)

    manifest = {
        "created_utc": datetime.now(UTC).isoformat(),
        "status": "complete",
        "row": f"X_A0_U4_{args.p_id}_{args.sensor}_{args.imu_filter}_{args.t_id}",
        "seeds": seeds,
        "export_type": "proxy_corrected_kinematics",
        "important_limit": "Acceleration correction is a world-frame fused-trajectory proxy, not a calibrated hardware body-frame accelerometer solution. Gyro correction is a simulated bias-removal proxy because full orientation truth/body-frame angular velocity is not in this dataset path.",
        "files": {
            "all_seeds": f"proxy_corrected_kinematics_{args.sensor}_{args.imu_filter}_{args.p_id}_{args.t_id}_ALL_SEEDS.csv",
            "summary": "proxy_corrected_kinematics_summary.csv",
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"out_dir": str(out_dir), **manifest}, indent=2), flush=True)


if __name__ == "__main__":
    main()
