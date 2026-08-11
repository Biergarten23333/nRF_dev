#!/usr/bin/env python3
"""Run the deterministic v47 state-adaptive Fusion small experiment.

This program is offline-only.  It consumes the lossless host capture and the
capture-bound T4 position replay; it contains no serial, BLE or probe code.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing as mp
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from v47_real_data_adapter import NODES, imu_physical, load_capture, sequence_gap_count
from v47_static_fusion import (
    InertialConfig, fit_node_clock, intervals_to_mask, local_to_t0_s,
    replay_inertial,
)
from v47_state_adaptive_fusion import AdaptiveParameters, StateAdaptiveFusion


RAW_SHA = "c5c7c923e2e29ad43d2d5e51217dda0ea1df8f95bdc04d30656f8055b038a9b8"
CAPTURE = "v47_full_system_30m_20260811_130843"
CALIBRATION = (1.0, 240.0)
HELD_OUT = (240.0, 484.0)
COMMON_STATIC = (1.0, 484.0)
POST_MOVE = (506.0, 535.0)
MOVES = {"BSFC2CC": (492.0, 498.0), "BSFAA61": (494.0, 500.0)}
MODES = ("B0", "I0", "I1", "H2", "H3", "H5", "S1")
_RUN_CONTEXT: dict = {}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(4 << 20):
            h.update(block)
    return h.hexdigest()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def clean(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return float(f"{value:.12g}")
    if isinstance(value, np.ndarray):
        return [clean(v) for v in value.tolist()]
    return value


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(clean(obj), indent=2, sort_keys=True,
                               allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: clean(row.get(key, "")) for key in fields})


def read_positions(path: Path) -> dict[str, dict[str, np.ndarray]]:
    lists = {node: {"t": [], "p": [], "status": [], "sweep": []} for node in NODES}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["solver"] != "UWB_TAG_T4":
                continue
            node = row["node"]
            lists[node]["t"].append(float(row["t0_s"]))
            lists[node]["p"].append([float(row["x_mm"]) / 1000.0,
                                     float(row["y_mm"]) / 1000.0,
                                     float(row["z_mm"]) / 1000.0])
            lists[node]["status"].append(row["solver_status"])
            lists[node]["sweep"].append(int(row["sweep"]))
    return {node: {"t": np.asarray(v["t"]), "p": np.asarray(v["p"]),
                   "status": np.asarray(v["status"]), "sweep": np.asarray(v["sweep"])}
            for node, v in lists.items()}


def annotations(prior: Path) -> tuple[dict[str, list[tuple[float, float]]], list[dict]]:
    static = {node: [] for node in NODES}
    with (prior / "STATIC_SEGMENTS.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            static[row["node"]].append((float(row["start_s"]), float(row["end_s"])))
    with (prior / "DISTURBANCE_EVENTS.csv").open(newline="", encoding="utf-8") as f:
        table = [r for r in csv.DictReader(f)
                 if r["classification"] == "TABLE_COMMON_MODE_VIBRATION"]
    if len(table) != 38:
        raise RuntimeError(f"expected 38 frozen table events, got {len(table)}")
    return static, table


def rolling_features(acc_g: np.ndarray, gyro_dps: np.ndarray, gravity_g: float,
                     stride: int = 10, window: int = 100) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Return causal 0.5 s features at 20 Hz without smoothing estimator inputs."""
    idx = np.arange(window - 1, len(acc_g), stride, dtype=np.int64)
    gyro_norm = np.linalg.norm(gyro_dps, axis=1)
    accel_dev = np.abs(np.linalg.norm(acc_g, axis=1) - gravity_g)

    def mean_std(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        cs = np.r_[0.0, np.cumsum(values)]
        cs2 = np.r_[0.0, np.cumsum(values * values)]
        starts = idx + 1 - window
        means = (cs[idx + 1] - cs[starts]) / window
        second = (cs2[idx + 1] - cs2[starts]) / window
        return means, np.sqrt(np.maximum(0.0, second - means * means))

    _, gyro_std = mean_std(gyro_norm)
    _, accel_std = mean_std(accel_dev)
    gyro_sq, _ = mean_std(gyro_norm * gyro_norm)
    accel_sq, _ = mean_std(accel_dev * accel_dev)
    return idx, {"gyro_rms_dps": np.sqrt(gyro_sq),
                 "accel_dev_rms_g": np.sqrt(accel_sq),
                 "gyro_std_dps": gyro_std, "accel_std_g": accel_std}


def robust_scatter(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    median = np.median(points, axis=0)
    radial = np.linalg.norm(points - median, axis=1)
    return median, radial, float(np.sqrt(np.mean(radial**2))), float(np.quantile(radial, .95))


def derive_manifest(data_root: Path, positions_path: Path, prior: Path,
                    geometry_manifest: Path, layout: Path) -> dict:
    imu, uwb, raw_audit = load_capture(data_root)
    positions = read_positions(positions_path)
    _, table = annotations(prior)
    params = {}
    for node in NODES:
        if len(positions[node]["p"]) != len(uwb[node]):
            raise RuntimeError(f"T4/raw count mismatch for {node}")
        if not np.array_equal(positions[node]["sweep"], uwb[node]["sweep"].astype(np.int64)):
            raise RuntimeError(f"T4/raw sweep mismatch for {node}")
        clock = fit_node_clock(uwb[node])
        imu_t = local_to_t0_s(imu[node]["b306_us"], clock)
        uwb_t = local_to_t0_s(uwb[node]["strobe_us"], clock)
        acc, gyro, _ = imu_physical(imu[node])
        cal_imu = (imu_t >= CALIBRATION[0]) & (imu_t < CALIBRATION[1])
        gyro_bias = np.median(gyro[cal_imu], axis=0)
        gravity = float(np.median(np.linalg.norm(acc[cal_imu], axis=1)))
        fidx, feat = rolling_features(acc, gyro - gyro_bias, gravity)
        ft = imu_t[fidx]
        clean_cal = (ft >= CALIBRATION[0]) & (ft < CALIBRATION[1])
        for event in table:
            start, end = float(event["onset_s"]), float(event["end_s"])
            clean_cal &= ~((ft >= start) & (ft < end))
        feature_thresholds = {key: float(np.quantile(values[clean_cal], .995))
                              for key, values in feat.items()}
        cal_p = positions[node]["p"][(uwb_t >= CALIBRATION[0]) & (uwb_t < CALIBRATION[1])]
        median = np.median(cal_p, axis=0)
        sigma = np.maximum(0.020, 1.4826 * np.median(np.abs(cal_p - median), axis=0))
        _, radial, _, _ = robust_scatter(cal_p)
        radial_sigma = float(1.4826 * np.median(np.abs(radial - np.median(radial))))
        params[node] = {
            "gyro_bias_dps": gyro_bias.tolist(), "local_gravity_g": gravity,
            "gyro_rms_threshold_dps": max(feature_thresholds["gyro_rms_dps"], .05),
            "accel_dev_rms_threshold_g": max(feature_thresholds["accel_dev_rms_g"], .001),
            "gyro_std_threshold_dps": max(feature_thresholds["gyro_std_dps"], .02),
            "accel_std_threshold_g": max(feature_thresholds["accel_std_g"], .0005),
            "uwb_sigma_xyz_m": sigma.tolist(),
            "uwb_r_m2": np.diag(sigma**2).tolist(),
            "platform_stability_threshold_m": max(.10, 2.5 * radial_sigma),
            "platform_shift_threshold_m": max(.18, 2.0 * float(np.linalg.norm(sigma))),
            "calibration_t4_median_m": median.tolist(),
            "calibration_observations": len(cal_p),
        }
    return {
        "schema": "biospur-v47-state-adaptive-parameter-manifest-v1",
        "frozen": True, "canonical_threshold_scale": 1.0,
        "source_head_before_experiment": git_head(),
        "historical_source_commit": "1b80923d1afe483f3be5d1afc18d6ef8ea6c5802",
        "input": {
            "capture": CAPTURE, "raw_sha256": raw_audit.raw_sha256,
            "positions_path": str(positions_path), "positions_sha256": sha256(positions_path),
            "geometry_manifest_path": str(geometry_manifest),
            "geometry_manifest_sha256": sha256(geometry_manifest),
            "layout_path": str(layout), "layout_sha256": sha256(layout),
            "prior_static_analysis_path": str(prior),
        },
        "windows_half_open_s": {"calibration": list(CALIBRATION),
                                  "held_out_static": list(HELD_OUT),
                                  "common_static": list(COMMON_STATIC),
                                  "post_move": list(POST_MOVE)},
        "global": {
            "state": "position_xyz_m_velocity_xyz_mps",
            "propagation": "constant_velocity_actual_hardware_timestamps",
            "stationary_accel_sigma_mps2": .03,
            "moving_accel_sigma_mps2": 1.0,
            "settling_accel_sigma_mps2": .25,
            "zupt_sigma_mps": .02,
            "stationary_speed_threshold_mps": .25,
            "nis_gate_chi2_dof3_p999": 16.266236,
            "exit_dwell_s": .75, "moving_quiet_dwell_s": .75,
            "settling_dwell_s": 2.0, "consensus_window_s": 2.0,
            "consensus_min_observations": 8, "consensus_update_period_s": 1.0,
            "zaru": "UNAVAILABLE_NO_ANGULAR_RATE_STATE_IN_S1",
            "coordinate_contract": "RELATIVE_GEOMETRY_ONLY",
            "sensor_to_v4_transform_status": "BLOCKED_FRAME_BINDING",
            "full_vector_inertial_propagation": "BLOCKED_FRAME_BINDING",
        },
        "derivation": {
            "imu_thresholds": "per-node 99.5 percentile causal 0.5 s features in calibration, excluding frozen table intervals",
            "uwb_covariance": "per-axis max(0.020 m, 1.4826*MAD) of T4 calibration positions",
            "platform_thresholds": "calibration robust radial scatter only; no movement outcomes used",
            "motion_windows_used_for_tuning": False,
        },
        "per_node": params,
    }


def adaptive_params(manifest: dict, node: str) -> AdaptiveParameters:
    n, g = manifest["per_node"][node], manifest["global"]
    return AdaptiveParameters(
        uwb_r_m2=np.asarray(n["uwb_r_m2"]),
        gyro_rms_threshold_dps=n["gyro_rms_threshold_dps"],
        accel_dev_rms_threshold_g=n["accel_dev_rms_threshold_g"],
        gyro_std_threshold_dps=n["gyro_std_threshold_dps"],
        accel_std_threshold_g=n["accel_std_threshold_g"],
        platform_stability_threshold_m=n["platform_stability_threshold_m"],
        platform_shift_threshold_m=n["platform_shift_threshold_m"],
        nis_gate=g["nis_gate_chi2_dof3_p999"], exit_dwell_s=g["exit_dwell_s"],
        moving_quiet_dwell_s=g["moving_quiet_dwell_s"], settling_dwell_s=g["settling_dwell_s"],
        consensus_window_s=g["consensus_window_s"],
        consensus_min_observations=g["consensus_min_observations"],
        consensus_update_period_s=g["consensus_update_period_s"],
        stationary_accel_sigma_mps2=g["stationary_accel_sigma_mps2"],
        moving_accel_sigma_mps2=g["moving_accel_sigma_mps2"],
        settling_accel_sigma_mps2=g["settling_accel_sigma_mps2"],
        zupt_sigma_mps=g["zupt_sigma_mps"],
        stationary_speed_threshold_mps=g["stationary_speed_threshold_mps"],
    )


def run_node(node: str, imu: np.ndarray, uwb: np.ndarray, pos: dict,
             static_intervals: list[tuple[float, float]], table: list[dict],
             manifest: dict) -> dict:
    clock = fit_node_clock(uwb)
    imu_t = local_to_t0_s(imu["b306_us"], clock)
    uwb_t = local_to_t0_s(uwb["strobe_us"], clock)
    acc, gyro, _ = imu_physical(imu)
    npar = manifest["per_node"][node]
    gyro_residual = gyro - np.asarray(npar["gyro_bias_dps"])
    fidx, features = rolling_features(acc, gyro_residual, npar["local_gravity_g"])
    control_t = imu_t[fidx]

    # Frozen I1 evidence, with all table-vibration and genuine-motion windows
    # removed for every node.  This makes the no-ZUPT safety claim explicit.
    stationary = intervals_to_mask(imu_t, static_intervals)
    forbidden = np.zeros(len(imu_t), dtype=bool)
    for e in table:
        forbidden |= intervals_to_mask(imu_t, [(float(e["onset_s"]), float(e["end_s"]))])
    for interval in MOVES.values():
        forbidden |= intervals_to_mask(imu_t, [interval])
    stationary &= ~forbidden
    i0 = replay_inertial(imu, acc, gyro, imu_t, stationary,
                         InertialConfig(zupt=False))
    i1 = replay_inertial(imu, acc, gyro, imu_t, stationary,
                         InertialConfig(zupt=True))

    estimator = StateAdaptiveFusion(adaptive_params(manifest, node))
    ci = ui = 0
    while ci < len(fidx) or ui < len(uwb_t):
        if ui < len(uwb_t) and (ci >= len(fidx) or uwb_t[ui] < control_t[ci]):
            ok = pos["status"][ui] == "ok" and np.isfinite(pos["p"][ui]).all()
            estimator.process_uwb(float(uwb_t[ui]), pos["p"][ui] if ok else None,
                                  status="ok" if ok else str(pos["status"][ui]),
                                  record_index=ui)
            ui += 1
        else:
            feat = {key: float(values[ci]) for key, values in features.items()}
            estimator.process_control(float(control_t[ci]), feat,
                                      sequence_advancing=True)
            ci += 1

    for row in estimator.audit:
        row["node"] = node
    for row in estimator.transitions:
        row["node"] = node
    snap_t = np.asarray([r["time_s"] for r in estimator.snapshots])
    snap_p = np.asarray([r["x_m"][:3] for r in estimator.snapshots])
    snap_v = np.asarray([r["velocity_mps"] for r in estimator.snapshots])
    snap_state = np.asarray([r["state"] for r in estimator.snapshots])
    return {"clock": clock, "imu_t": imu_t, "uwb_t": uwb_t, "i0": i0, "i1": i1,
            "s1": estimator, "snap_t": snap_t, "snap_p": snap_p,
            "snap_v": snap_v, "snap_state": snap_state,
            "forbidden_zupt_updates": int(np.sum(i1["zupt_count_by_second"][[
                sec for sec in range(1801) if any(a <= sec < b for a, b in
                    list(MOVES.values()) + [(float(e["onset_s"]), float(e["end_s"])) for e in table])
            ]])),
            "imu_sequence_gaps": sequence_gap_count(imu["seq"], 1 << 16),
            "uwb_sweep_gaps": sequence_gap_count(uwb["sweep"], 1 << 32),
        }


def _run_node_worker(node: str) -> tuple[str, dict]:
    c = _RUN_CONTEXT
    return node, run_node(node, c["imu"][node], c["uwb"][node], c["positions"][node],
                          c["static"][node], c["table"], c["manifest"])


def trace_metrics(t: np.ndarray, p: np.ndarray, v: np.ndarray,
                  window=HELD_OUT) -> tuple[float, float, float, float]:
    q = (t >= window[0]) & (t < window[1]) & np.isfinite(p[:, 0])
    _, _, rms, p95 = robust_scatter(p[q])
    speed = np.linalg.norm(v[q], axis=1)
    return rms, p95, float(np.sqrt(np.mean(speed**2))), float(np.quantile(speed, .95))


def build_metrics(results: dict, positions: dict) -> list[dict]:
    rows = []
    for node in NODES:
        r = results[node]
        for mode in MODES:
            row = {"node": node, "mode": mode, "coordinate_frame": "V4_IO_RELATIVE"
                   if mode in ("B0", "S1") else "LOCAL_INERTIAL_OR_BLOCKED"}
            if mode == "B0":
                q = (r["uwb_t"] >= HELD_OUT[0]) & (r["uwb_t"] < HELD_OUT[1])
                _, _, prms, pp95 = robust_scatter(positions[node]["p"][q])
                row.update(status="OK_INPUT_BASELINE", position_rms_m=prms,
                           position_p95_m=pp95, velocity_rms_mps="", velocity_p95_mps="",
                           stationary_occupancy="", zupt_updates=0, zaru_updates=0,
                           uwb_accepted=int(np.sum(q)), uwb_rejected=0,
                           covariance_min_eigenvalue="", covariance_max_asymmetry="",
                           reinitializations=0)
            elif mode in ("I0", "I1"):
                tr = r[mode.lower()]
                vals = trace_metrics(tr["time_s"], tr["position_m"], tr["velocity_mps"])
                row.update(status="LOCAL_FRAME_CONTROL_NOT_V4_COMPARABLE",
                           position_rms_m=vals[0], position_p95_m=vals[1],
                           velocity_rms_mps=vals[2], velocity_p95_mps=vals[3],
                           stationary_occupancy="", zupt_updates=tr["zupt_updates"], zaru_updates=0,
                           uwb_accepted=0, uwb_rejected=0,
                           covariance_min_eigenvalue=tr["covariance_min_eigenvalue"],
                           covariance_max_asymmetry=tr["covariance_max_asymmetry"],
                           reinitializations=0)
            elif mode in ("H2", "H3", "H5"):
                row.update(status="BLOCKED_FRAME_BINDING", position_rms_m="", position_p95_m="",
                           velocity_rms_mps="", velocity_p95_mps="", stationary_occupancy="",
                           zupt_updates=0, zaru_updates=0, uwb_accepted=0, uwb_rejected=0,
                           covariance_min_eigenvalue="", covariance_max_asymmetry="",
                           reinitializations=0)
            else:
                s = r["s1"]
                vals = trace_metrics(r["snap_t"], r["snap_p"], r["snap_v"])
                q = (r["snap_t"] >= HELD_OUT[0]) & (r["snap_t"] < HELD_OUT[1])
                aq = [a for a in s.audit if HELD_OUT[0] <= a["time_s"] < HELD_OUT[1]]
                row.update(status="OK_POSITION_VELOCITY_IMU_EVIDENCE",
                           position_rms_m=vals[0], position_p95_m=vals[1],
                           velocity_rms_mps=vals[2], velocity_p95_mps=vals[3],
                           stationary_occupancy=float(np.mean(r["snap_state"][q] == "STATIONARY")),
                           zupt_updates=s.zupt_updates, zaru_updates=s.zaru_updates,
                           uwb_accepted=sum(a["category"] == "accepted" for a in aq),
                           uwb_rejected=sum(a["category"] == "rejected" for a in aq),
                           covariance_min_eigenvalue=s.covariance_min_eigenvalue,
                           covariance_max_asymmetry=s.covariance_max_asymmetry,
                           reinitializations=s.reinitializations)
            rows.append(row)
    return rows


def nearest_transition(transitions: list[dict], start: float, end: float,
                       old: str | None, new: str) -> dict | None:
    for tr in transitions:
        if start <= tr["time_s"] < end and tr["to_state"] == new and (old is None or tr["from_state"] == old):
            return tr
    return None


def movement_rows(results: dict, positions: dict) -> list[dict]:
    rows = []
    for node, (start, end) in MOVES.items():
        r, s = results[node], results[node]["s1"]
        pre_q = (r["uwb_t"] >= 450) & (r["uwb_t"] < 484)
        post_q = (r["uwb_t"] >= POST_MOVE[0]) & (r["uwb_t"] < POST_MOVE[1])
        pre = np.median(positions[node]["p"][pre_q], axis=0)
        post = np.median(positions[node]["p"][post_q], axis=0)
        delta = post - pre
        release = nearest_transition(s.transitions, start - 2, end + 3, "STATIONARY", "MOVING")
        relock = nearest_transition(s.transitions, end, end + 30, None, "STATIONARY")
        spost = np.median(r["snap_p"][(r["snap_t"] >= POST_MOVE[0]) & (r["snap_t"] < POST_MOVE[1])], axis=0)
        spre = np.median(r["snap_p"][(r["snap_t"] >= 450) & (r["snap_t"] < 484)], axis=0)
        sdelta = spost - spre
        direction = delta / max(np.linalg.norm(delta), 1e-12)
        post_points = positions[node]["p"][post_q]
        post_radial = np.linalg.norm(post_points - post, axis=1)
        reach_tolerance = max(.10, 2.0 * 1.4826 * float(np.median(post_radial)))
        reach = ""
        candidate = np.flatnonzero((r["snap_t"] >= start) &
                                   (np.linalg.norm(r["snap_p"] - post, axis=1) <= reach_tolerance))
        for idx in candidate:
            sustained = ((r["snap_t"] >= r["snap_t"][idx]) &
                         (r["snap_t"] < r["snap_t"][idx] + 1.0))
            if (np.sum(sustained) >= 4 and
                    np.all(np.linalg.norm(r["snap_p"][sustained] - post, axis=1) <= reach_tolerance)):
                reach = float(r["snap_t"][idx])
                break
        during = (r["snap_t"] >= start) & (r["snap_t"] < end + 10)
        projection = (r["snap_p"][during] - spost) @ direction
        overshoot = max(0.0, float(np.max(projection))) if len(projection) else math.nan
        base_time = relock["time_s"] if relock else POST_MOVE[0]
        base = np.median(r["snap_p"][(r["snap_t"] >= base_time) & (r["snap_t"] < base_time + 2)], axis=0)
        drift5 = np.median(r["snap_p"][(r["snap_t"] >= base_time + 5) & (r["snap_t"] < base_time + 7)], axis=0)
        drift30 = np.median(r["snap_p"][(r["snap_t"] >= base_time + 30) & (r["snap_t"] < base_time + 32)], axis=0)
        for mode in MODES:
            row = {"node": node, "mode": mode, "motion_start_s": start, "motion_end_s": end}
            if mode == "S1":
                row.update(status="OK" if release and relock else "DETECTION_INCOMPLETE",
                           motion_onset_s=release["time_s"] if release else "",
                           onset_latency_s=release["time_s"] - start if release else "",
                           lock_released_in_window=int(bool(release and release["time_s"] < end)),
                           new_platform_reach_s=reach,
                           new_platform_reach_latency_s=reach-start if reach != "" else "",
                           new_platform_tolerance_m=reach_tolerance,
                           relock_s=relock["time_s"] if relock else "",
                           relock_latency_s=relock["time_s"] - end if relock else "",
                           t4_dx_m=delta[0], t4_dy_m=delta[1], t4_dz_m=delta[2],
                           t4_displacement_m=np.linalg.norm(delta), s1_displacement_m=np.linalg.norm(sdelta),
                           displacement_ratio=np.linalg.norm(sdelta) / max(np.linalg.norm(delta), 1e-12),
                           overshoot_m=overshoot, drift_5s_m=np.linalg.norm(drift5 - base),
                           drift_30s_m=np.linalg.norm(drift30 - base),
                           returned_old_platform=int(np.linalg.norm(spost - pre) < np.linalg.norm(spost - post)))
            elif mode == "B0":
                row.update(status="INPUT_REFERENCE", t4_dx_m=delta[0], t4_dy_m=delta[1],
                           t4_dz_m=delta[2], t4_displacement_m=np.linalg.norm(delta))
            elif mode in ("H2", "H3", "H5"):
                row.update(status="BLOCKED_FRAME_BINDING")
            else:
                row.update(status="LOCAL_FRAME_NOT_V4_COMPARABLE")
            rows.append(row)
    return rows


def table_rows(results: dict, positions: dict, table: list[dict]) -> list[dict]:
    rows = []
    for event in table:
        start, end = float(event["onset_s"]), float(event["end_s"])
        for node in NODES:
            r, s = results[node], results[node]["s1"]
            b = (r["uwb_t"] >= start - 2) & (r["uwb_t"] < start)
            e = (r["uwb_t"] >= start) & (r["uwb_t"] < end)
            bmed = np.median(positions[node]["p"][b], axis=0)
            b0_exc = float(np.max(np.linalg.norm(positions[node]["p"][e] - bmed, axis=1))) if np.any(e) else math.nan
            sb = (r["snap_t"] >= start - 2) & (r["snap_t"] < start)
            se = (r["snap_t"] >= start) & (r["snap_t"] < end)
            smed = np.median(r["snap_p"][sb], axis=0)
            sexc = float(np.max(np.linalg.norm(r["snap_p"][se] - smed, axis=1))) if np.any(se) else math.nan
            vmax = float(np.max(np.linalg.norm(r["snap_v"][se], axis=1))) if np.any(se) else math.nan
            released = nearest_transition(s.transitions, start, end + 1, "STATIONARY", "MOVING")
            recovered = (nearest_transition(s.transitions, end, end + 30, None, "STATIONARY")
                         if released else None)
            after = (r["snap_t"] >= end + 5) & (r["snap_t"] < end + 10)
            persistent = float(np.linalg.norm(np.median(r["snap_p"][after], axis=0) - smed)) if np.any(after) else math.nan
            for mode in MODES:
                row = {"event_id": event["event_id"], "node": node, "mode": mode,
                       "onset_s": start, "end_s": end}
                if mode == "S1":
                    persistent_transition = bool(released and recovered and persistent > .15)
                    classification = ("STATIONARY_LOCK" if not released else
                                      "LOCK_RELEASE_RECOVERED" if recovered else
                                      "LOCK_RELEASE_NO_30S_RECOVERY")
                    row.update(status="OK", state_classification=classification,
                               lock_release=int(released is not None), max_position_excursion_m=sexc,
                               max_velocity_mps=vmax,
                               recovery_settling_s=(recovered["time_s"]-end if recovered else
                                                    (0.0 if not released else "")),
                               false_persistent_platform_transition=int(persistent_transition),
                               post_event_shift_m=persistent, reinitializations=s.reinitializations)
                elif mode == "B0":
                    row.update(status="INPUT_RESPONSE", max_position_excursion_m=b0_exc)
                elif mode in ("H2", "H3", "H5"):
                    row.update(status="BLOCKED_FRAME_BINDING")
                else:
                    tr = r[mode.lower()]
                    tq = (tr["time_s"] >= start) & (tr["time_s"] < end)
                    row.update(status="LOCAL_FRAME_CONTROL", max_velocity_mps=float(np.max(
                        np.linalg.norm(tr["velocity_mps"][tq], axis=1))) if np.any(tq) else "")
                rows.append(row)
    return rows


def mode_definitions() -> dict:
    return {
        "coordinate_contract": "RELATIVE_GEOMETRY_ONLY",
        "B0": {"definition": "canonical unsmoothed UWB_TAG_T4 position stream", "status": "RUN"},
        "I0": {"definition": "existing real-data pure inertial replay; actual B306 timestamps; no UWB", "status": "RUN_LOCAL_FRAME_CONTROL"},
        "I1": {"definition": "I0 plus explicit frozen-annotation ZUPT; table/motion windows excluded", "status": "RUN_LOCAL_FRAME_CONTROL"},
        "H2": {"t_number": "T2/T2Lite", "direction": "IMU_CORRECTS_UWB",
               "source_commit": "1b80923d1afe483f3be5d1afc18d6ef8ea6c5802",
               "source": "run_phase0_phase1_vertical_slice.py:relative_motion_filter; run_real_6axis_vertical_slice.py:forward_imu_corrects_uwb",
               "exact_update": "pred=out_prev+delta_imu; alpha=.42 below 180 mm innovation else .18",
               "status": "BLOCKED_FRAME_BINDING"},
        "H5": {"t_number": "T5/T5Lite", "direction": "UWB_CORRECTS_IMU",
               "source_commit": "1b80923d1afe483f3be5d1afc18d6ef8ea6c5802",
               "source": "run_real_6axis_vertical_slice.py:fuse_uwb_imu_packet",
               "state": "position(3), velocity(3), P(6x6), external yaw; no bias state",
               "exact_update": "IMU p/v propagation then solved-UWB H=[I,0], 3D NIS<25",
               "status": "BLOCKED_FRAME_BINDING"},
        "H3": {"t_number": "T3/T3Lite", "direction": "BIDIRECTIONAL_CORRECTION",
               "source_commit": "1b80923d1afe483f3be5d1afc18d6ef8ea6c5802",
               "source": "run_real_6axis_vertical_slice.py:forward_bidirectional_fusion",
               "exact_update": "0.68*T5Lite + 0.32*T2Lite at each UWB time",
               "independent_ekf": False, "status": "BLOCKED_FRAME_BINDING"},
        "S1": {"definition": "four-state p/v CV KF, IMU motion evidence, adaptive Q, async T4 updates, real ZUPT and robust stationary consensus", "status": "RUN"},
    }


def savefig(path: Path) -> None:
    plt.tight_layout()
    plt.savefig(path, metadata={"Date": None})
    plt.close()
    # Matplotlib emits path-data lines with trailing spaces.  Canonicalize the
    # text so generated evidence passes repository whitespace checks.
    svg = path.read_text(encoding="utf-8")
    path.write_text("\n".join(line.rstrip() for line in svg.splitlines()) + "\n",
                    encoding="utf-8")


def plots(out: Path, results: dict, positions: dict, table: list[dict]) -> None:
    matplotlib.rcParams["svg.hashsalt"] = "v47-state-adaptive-fusion-v1"
    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    for row, node in enumerate(("BSFC2CC", "BSFAA61")):
        r = results[node]; q = (r["uwb_t"] >= 480) & (r["uwb_t"] < 540)
        axes[row, 0].plot(positions[node]["p"][q, 0], positions[node]["p"][q, 1], ".", ms=1, alpha=.25, label="B0")
        sq = (r["snap_t"] >= 480) & (r["snap_t"] < 540)
        axes[row, 0].plot(r["snap_p"][sq, 0], r["snap_p"][sq, 1], lw=1, label="S1")
        axes[row, 0].set_title(node); axes[row, 0].set_xlabel("V4 x (m)"); axes[row, 0].set_ylabel("V4 y (m)"); axes[row, 0].legend()
        axes[row, 1].plot(r["snap_t"][sq], np.linalg.norm(r["snap_v"][sq], axis=1), lw=.8)
        axes[row, 1].set_xlabel("T0 seconds"); axes[row, 1].set_ylabel("S1 speed (m/s)")
    savefig(out / "movement_trajectories.svg")

    labels, b0, s1 = [], [], []
    for node in NODES:
        r = results[node]; q = (r["uwb_t"] >= HELD_OUT[0]) & (r["uwb_t"] < HELD_OUT[1])
        b0.append(robust_scatter(positions[node]["p"][q])[2] * 1000)
        s1.append(trace_metrics(r["snap_t"], r["snap_p"], r["snap_v"])[0] * 1000); labels.append(node[3:])
    x = np.arange(len(labels)); plt.figure(figsize=(12, 5)); plt.bar(x-.2, b0, .4, label="B0"); plt.bar(x+.2, s1, .4, label="S1")
    plt.xticks(x, labels); plt.ylabel("held-out robust-median RMS scatter (mm)"); plt.legend(); savefig(out / "static_scatter_comparison.svg")

    node = "BSFC2CC"; r = results[node]; q = (r["snap_t"] >= 475) & (r["snap_t"] < 545)
    state_num = np.array([{"INIT": 0, "STATIONARY": 1, "MOVING": 2, "SETTLING": 3}[x] for x in r["snap_state"]])
    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    axes[0].step(r["snap_t"][q], state_num[q], where="post"); axes[0].set_ylabel("state 0..3")
    axes[1].plot(r["snap_t"][q], np.linalg.norm(r["snap_v"][q], axis=1)); axes[1].set_ylabel("speed (m/s)")
    audit = r["s1"].audit; at = np.asarray([a["time_s"] for a in audit]); nis = np.asarray([float(a["nis"]) if a["nis"] != "" else np.nan for a in audit]); aq = (at >= 475) & (at < 545)
    axes[2].plot(at[aq], nis[aq], ".", ms=1); axes[2].set_ylabel("UWB NIS"); axes[2].set_xlabel("T0 seconds")
    savefig(out / "state_velocity_innovation_timeline.svg")

    e = table[0]; start, end = float(e["onset_s"]), float(e["end_s"])
    plt.figure(figsize=(12, 6))
    for node in NODES:
        r = results[node]; q = (r["snap_t"] >= start-3) & (r["snap_t"] < end+5)
        base = np.median(r["snap_p"][(r["snap_t"] >= start-2) & (r["snap_t"] < start)], axis=0)
        plt.plot(r["snap_t"][q], np.linalg.norm(r["snap_p"][q] - base, axis=1)*1000, lw=.8, label=node)
    plt.axvspan(start, end, color="orange", alpha=.15); plt.xlabel("T0 seconds"); plt.ylabel("S1 excursion (mm)"); plt.legend(ncol=5, fontsize=7)
    savefig(out / "representative_table_vibration.svg")


def documents(out: Path, metrics: list[dict], movement: list[dict], table_rows_: list[dict],
              integrity: dict) -> None:
    b = [r for r in metrics if r["mode"] == "B0"]
    s = [r for r in metrics if r["mode"] == "S1"]
    bmed = float(np.median([r["position_rms_m"] for r in b]))
    smed = float(np.median([r["position_rms_m"] for r in s]))
    move_s = [r for r in movement if r["mode"] == "S1"]
    false_table = sum(int(r.get("false_persistent_platform_transition", 0) or 0)
                      for r in table_rows_ if r["mode"] == "S1")
    releases = all(r.get("lock_released_in_window") == 1 for r in move_s)
    relocks = all(r.get("relock_s", "") != "" for r in move_s)
    numerical = bool(integrity["all_finite_psd_symmetric"] and integrity["observation_accounting_closed"])
    verdict = "CONDITIONAL_PASS" if numerical and smed < bmed and releases and relocks and false_table == 0 else "FUSION_SMALL_EXPERIMENT_FAIL"
    decision = {
        "verdict": verdict, "static_b0_median_rms_m": bmed,
        "static_s1_median_rms_m": smed, "static_rms_reduction_fraction": 1-smed/bmed,
        "both_moves_release_in_window": releases, "both_moves_relock": relocks,
        "table_false_persistent_transitions": false_table,
        "full_vector_inertial_propagation": "BLOCKED_FRAME_BINDING",
        "historical_direction_to_inherit": "T5_DIRECTION_ONLY_AS_ARCHITECTURAL_PRECEDENT",
        "historical_algorithms_to_retire": ["T2Lite fixed-alpha output correction", "T3Lite fixed output blend"],
        "known_manual_trajectory_capture": "JUSTIFIED_CONDITIONALLY_FOR_FRAME_BINDING_AND_GROUND_TRUTH",
    }
    write_json(out / "DECISION.json", decision)
    (out / "FRAME_BINDING_AUDIT.md").write_text(
        "# Sensor-to-V4 frame binding audit\n\n"
        "The current-room Anchor geometry is capture-bound and authorized as `RELATIVE_GEOMETRY_ONLY`. "
        "It does not bind any B306/JY61P sensor axis to V4-io. A single static gravity vector constrains "
        "two tilt degrees of freedom only; yaw remains unobservable, the boards may have different headings, "
        "and V4 +Z has not been surveyed as physical up. The two unknown moves have no external attitude or "
        "trajectory truth. Consequently the signed sensor-to-V4 transform is not identifiable.\n\n"
        "Main S1 therefore uses CV propagation in V4, IMU only as independent stationarity/motion evidence, "
        "and timestamped T4 positions as asynchronous measurements. No acceleration vector is rotated into V4. "
        "Full vector propagation and spatial H2/H5/H3 reproduction are `BLOCKED_FRAME_BINDING`.\n",
        encoding="utf-8")
    (out / "HISTORICAL_REPRODUCTION.md").write_text(
        "# Historical reproduction audit\n\n"
        "All historical controls map to commit `1b80923d1afe483f3be5d1afc18d6ef8ea6c5802`. "
        "H2 is the literal T2Lite IMU-corrects-UWB output recursion: IMU spatial delta predicts the next "
        "solved position and alpha is 0.42 below a 180 mm innovation, otherwise 0.18. H5 is T5Lite: "
        "a six-state position/velocity covariance, external scalar yaw, no bias state, IMU propagation, and "
        "solved-UWB `H=[I,0]` correction with 3-D NIS below 25. H3 is exactly the output blend "
        "`0.68*T5Lite + 0.32*T2Lite`; it is not a third EKF.\n\n"
        "The common real-data adapter supplies actual timestamps and T4 observations, but each historical "
        "spatial path requires an IMU displacement/propagation vector expressed in V4. That transform is "
        "unavailable, so all three were run through the common binding gate and terminated as "
        "`BLOCKED_FRAME_BINDING` rather than silently inventing a rotation. T5's measurement direction is "
        "the closest architectural ancestor of S1; its literal implementation is not adopted. T2Lite and "
        "the fixed T3Lite blend should be retired. T6/T8 remain separate raw-range prototypes and T11/I0 "
        "is the IMU-only control.\n", encoding="utf-8")
    report = f"""# v47 state-adaptive Fusion small experiment

## Verdict

`{verdict}`. This is an offline relative-geometry experiment, not an absolute localization-accuracy claim.

The held-out median ten-node RMS scatter is {bmed*1000:.3f} mm for unsmoothed B0 and {smed*1000:.3f} mm for S1 ({(1-smed/bmed)*100:.2f}% reduction). Both annotated node moves released the stationary lock in-window: **{releases}**; both reacquired a stationary platform: **{relocks}**. Across 38 frozen table-vibration intervals and ten nodes, false persistent S1 platform transitions: **{false_table}**.

## Interpretation

S1 propagates position/velocity on reconstructed hardware time, uses IMU only for independently sampled motion evidence, performs real zero-velocity measurements while stationary, buffers individual stationary T4 points into a robust slow consensus, and applies gated Kalman position updates while moving. Thus UWB constrains drift without overwriting every high-rate state. No filter reset is used for normal transitions.

I0 and I1 are valid local inertial controls but their arbitrary local frames cannot be numerically compared with V4 position. H2, H5 and H3 retain their exact historical definitions and are explicitly blocked at the spatial frame-binding gate. T5's UWB-corrects-IMU direction most closely matches the desired architecture; T2's fixed alpha and T3's fixed output blend should not be inherited.

Full vector inertial propagation remains `BLOCKED_FRAME_BINDING`. A known manual trajectory capture is justified conditionally, specifically to measure the sensor/body-to-V4 transform and provide independent trajectory evidence.

## Calibration required before human-body IK/FK

Required work is: a surveyed V4 gravity/up direction; signed sensor-axis verification; per-mount board-to-body extrinsics including yaw; accelerometer six-face scale/misalignment calibration; gyro bias, temperature and scale characterization; lever arms; hardware-time validation against the trajectory reference; dynamic per-link UWB covariance/outlier characterization; and an external ground-truth manual trajectory. One static pose is not a complete calibration.

All windows are half-open `[start,end)`. Parameters use only T0+1–240 s with frozen table intervals excluded. T0+240–484 s, both moves, and later data were not used for threshold tuning. Raw evidence was read only.
"""
    (out / "REPORT.md").write_text(report, encoding="utf-8")


def replay(data_root: Path, positions_path: Path, prior: Path, manifest_path: Path,
           out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("frozen") or manifest["input"]["raw_sha256"] != RAW_SHA:
        raise RuntimeError("parameter manifest is not frozen against authoritative raw")
    imu, uwb, raw_audit = load_capture(data_root)
    positions = read_positions(positions_path)
    static, table = annotations(prior)
    global _RUN_CONTEXT
    _RUN_CONTEXT = {"imu": imu, "uwb": uwb, "positions": positions,
                    "static": static, "table": table, "manifest": manifest}
    # Fork shares the immutable replay arrays copy-on-write and keeps the host
    # responsive while the two inertial controls dominate CPU time.
    with mp.get_context("fork").Pool(processes=5) as pool:
        results = dict(pool.map(_run_node_worker, NODES))
    _RUN_CONTEXT = {}
    metrics = build_metrics(results, positions)
    movement = movement_rows(results, positions)
    tabrows = table_rows(results, positions, table)
    transitions = [{**tr} for node in NODES for tr in results[node]["s1"].transitions]
    audit = [{**a} for node in NODES for a in results[node]["s1"].audit]
    per_node = {}
    for node in NODES:
        r, s = results[node], results[node]["s1"]
        cats = {key: sum(a["category"] == key for a in s.audit)
                for key in ("accepted", "rejected", "invalid", "unavailable")}
        per_node[node] = {
            "imu_samples": len(imu[node]), "uwb_inputs": len(uwb[node]),
            "imu_sequence_gaps": r["imu_sequence_gaps"], "uwb_sweep_gaps": r["uwb_sweep_gaps"],
            "timestamp_order_violations": s.negative_dt, "extreme_dt": s.extreme_dt,
            "uwb_classification": cats, "uwb_accounting_closed": sum(cats.values()) == len(uwb[node]),
            "uwb_reasons": dict(sorted(__import__("collections").Counter(
                a["reason"] for a in s.audit).items())),
            "forbidden_i1_zupt_updates": r["forbidden_zupt_updates"],
            "covariance_min_eigenvalue": s.covariance_min_eigenvalue,
            "covariance_max_asymmetry": s.covariance_max_asymmetry,
            "reinitializations": s.reinitializations,
        }
    integrity = {
        "schema": "biospur-v47-state-adaptive-numerical-integrity-v1",
        "raw_sha256_at_replay": raw_audit.raw_sha256,
        "all_finite_psd_symmetric": all(v["covariance_min_eigenvalue"] >= -1e-10 and v["covariance_max_asymmetry"] <= 1e-10 for v in per_node.values()),
        "observation_accounting_closed": all(v["uwb_accounting_closed"] for v in per_node.values()),
        "deterministic_replay_equality": "PENDING_EXTERNAL_BYTE_COMPARISON",
        "all_i1_forbidden_zupt_zero": all(v["forbidden_i1_zupt_updates"] == 0 for v in per_node.values()),
        "per_node": per_node,
    }
    write_json(out / "PARAMETER_MANIFEST.json", manifest)
    write_json(out / "MODE_DEFINITIONS.json", mode_definitions())
    write_csv(out / "PER_NODE_MODE_METRICS.csv", metrics, ["node", "mode", "status", "coordinate_frame", "position_rms_m", "position_p95_m", "velocity_rms_mps", "velocity_p95_mps", "stationary_occupancy", "zupt_updates", "zaru_updates", "uwb_accepted", "uwb_rejected", "covariance_min_eigenvalue", "covariance_max_asymmetry", "reinitializations"])
    write_csv(out / "STATE_TRANSITIONS.csv", transitions, ["node", "time_s", "from_state", "to_state", "reason"])
    write_csv(out / "UWB_UPDATE_AUDIT.csv", audit, ["node", "record_index", "time_s", "state", "category", "nis", "update_applied", "reason"])
    write_csv(out / "MOVEMENT_RESPONSE.csv", movement, ["node", "mode", "status", "motion_start_s", "motion_end_s", "motion_onset_s", "onset_latency_s", "lock_released_in_window", "new_platform_reach_s", "new_platform_reach_latency_s", "new_platform_tolerance_m", "relock_s", "relock_latency_s", "t4_dx_m", "t4_dy_m", "t4_dz_m", "t4_displacement_m", "s1_displacement_m", "displacement_ratio", "overshoot_m", "drift_5s_m", "drift_30s_m", "returned_old_platform"])
    write_csv(out / "TABLE_VIBRATION_RESPONSE.csv", tabrows, ["event_id", "node", "mode", "status", "onset_s", "end_s", "state_classification", "lock_release", "max_position_excursion_m", "max_velocity_mps", "recovery_settling_s", "false_persistent_platform_transition", "post_event_shift_m", "reinitializations"])
    write_json(out / "NUMERICAL_INTEGRITY.json", integrity)
    plots(out, results, positions, table)
    documents(out, metrics, movement, tabrows, integrity)
    names = sorted(p.name for p in out.iterdir() if p.is_file() and p.name != "SHA256SUMS")
    (out / "SHA256SUMS").write_text("".join(f"{sha256(out/name)}  {name}\n" for name in names), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    derive = sub.add_parser("derive")
    run = sub.add_parser("replay")
    for p in (derive, run):
        p.add_argument("--data-root", type=Path, required=True)
        p.add_argument("--positions", type=Path, required=True)
        p.add_argument("--prior", type=Path, required=True)
    derive.add_argument("--geometry-manifest", type=Path, required=True)
    derive.add_argument("--layout", type=Path, required=True)
    derive.add_argument("--out", type=Path, required=True)
    run.add_argument("--parameters", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "derive":
        write_json(args.out, derive_manifest(args.data_root, args.positions, args.prior,
                                             args.geometry_manifest, args.layout))
    else:
        replay(args.data_root, args.positions, args.prior, args.parameters, args.out)


if __name__ == "__main__":
    main()
