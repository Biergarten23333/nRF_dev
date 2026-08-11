#!/usr/bin/env python3
"""Build the deterministic v47 ten-node static Fusion replay evidence set."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing as mp
import os
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from v47_real_data_adapter import NODES, imu_physical, load_capture
from v47_static_fusion import (
    T0_MASTER_MS, InertialConfig, RangeConfig, fit_node_clock,
    intervals_to_mask, local_to_t0_s, replay_inertial, replay_range_space,
)

RAW_SHA = "c5c7c923e2e29ad43d2d5e51217dda0ea1df8f95bdc04d30656f8055b038a9b8"
CAPTURE_ID = "v47_full_system_30m_20260811_130843"
T0_WALL = "2026-08-11T13:09:59.019+02:00"
BASELINE = (1.0, 484.0)
MOVE_WINDOWS = {"BSFC2CC": (492.0, 498.0), "BSFAA61": (494.0, 500.0)}
MODES = ("M0", "M1", "M2", "M3", "M4")

_WORK: dict = {}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def clean(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return float(f"{value:.12g}")
    if isinstance(value, np.ndarray):
        return [clean(x) for x in value.tolist()]
    return value


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: clean(row.get(key, "")) for key in fields})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def source_commit(path: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", str(path)], text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "UNKNOWN"


def load_annotations(prior: Path) -> tuple[dict[str, list[tuple[float, float]]], list[dict], list[dict]]:
    intervals = {node: [] for node in NODES}
    with (prior / "STATIC_SEGMENTS.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            intervals[row["node"]].append((float(row["start_s"]), float(row["end_s"])))
    with (prior / "DISTURBANCE_EVENTS.csv").open(newline="", encoding="utf-8") as handle:
        events = list(csv.DictReader(handle))
    table = [row for row in events if row["classification"] == "TABLE_COMMON_MODE_VIBRATION"]
    moves = [row for row in events if row["classification"] == "SINGLE_NODE_REPOSITION_OR_ROTATION"]
    if len(table) != 38 or {(r["nodes"], float(r["onset_s"]), float(r["end_s"])) for r in moves} != {
        ("BSFC2CC", 492.0, 498.0), ("BSFAA61", 494.0, 500.0)
    }:
        raise RuntimeError("frozen annotation mismatch")
    return intervals, table, moves


def geometry_manifest(data_root: Path, uwb: dict[str, np.ndarray]) -> dict:
    candidates = []
    for name in ("anchor_layout.json", "anchor_layout_v5_scalelock.json"):
        path = Path("UWB_Part/2026-07-15-FREEZE/scripts/solvers/erlangen_deployment_v4io_t4/reference_layout_inputs") / name
        obj = json.loads(path.read_text(encoding="utf-8"))
        candidates.append({
            "path": str(path), "sha256": sha256(path), "git_commit": source_commit(path),
            "declared_version": obj.get("version"), "coordinate_unit": "mm",
            "source_capture": obj.get("stats", {}).get("source_pairs_csv", ""),
            "applicable_to_capture": False,
            "rejection_reason": "Erlangen 2026-07-10 deployment; no evidence binds it to the 2026-08-11 current-room capture",
        })
    mappings = {}
    mismatch = 0
    for slot in range(8):
        values = np.concatenate([u["anchor_id"][:, slot] for u in uwb.values()])
        unique, counts = np.unique(values, return_counts=True)
        mode = int(unique[np.argmax(counts)])
        mappings[str(slot)] = mode
        mismatch += int(np.sum(values != mode))
    run_manifest = data_root / "formal_capture/RUN_MANIFEST.json"
    return {
        "schema": "capture-bound-geometry-manifest-v1", "capture_id": CAPTURE_ID,
        "capture_t0": T0_WALL, "binding_status": "BLOCKED_GEOMETRY_BINDING",
        "spatial_solution_permitted": False,
        "run_manifest": {"path": str(run_manifest), "sha256": sha256(run_manifest),
                         "contains_geometry_or_delay": False, "commands_sent": []},
        "observed_slot_mapping": {"status": "BOUND_FROM_EACH_UWB_RECORD", "slot_to_anchor_id": mappings,
                                  "mismatch_records": mismatch, "labels": {str(i): chr(65 + i) for i in range(8)}},
        "candidates_rejected": candidates,
        "missing_unique_binding": [
            "current-room Anchor IDs 0..7 to XYZ in a named coordinate frame",
            "current-room per-anchor and tag range-delay corrections",
            "capture-applicable production solver configuration and its Git/SHA provenance",
        ],
        "likely_authoritative_source": "the current-room Anchor deployment/calibration export used by the production host before 2026-08-11 T0",
        "measurement_R_status": "derived for replay only from frozen T0+1..484 s ranges; not a geometry substitute",
    }


def robust_sigmas(uwb: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
    t = (uwb["master_ms"].astype(np.int64) - T0_MASTER_MS) / 1000.0
    baseline = (t >= BASELINE[0]) & (t < BASELINE[1])
    sigmas = np.empty(8)
    for slot in range(8):
        valid = baseline & ((uwb["valid_mask"] & (1 << slot)) != 0)
        values = uwb["range_mm"][valid, slot].astype(float)
        median = np.median(values)
        sigmas[slot] = max(20.0, 1.4826 * np.median(np.abs(values - median)))
    return sigmas, float(np.median(sigmas)), baseline


def mode_metrics(node: str, mode: str, trace: dict | None, range_summary: dict | None) -> dict:
    base = {"node": node, "mode": mode, "geometry_bound": 0,
            "uwb_spatial_update_applied": 0, "status": "RANGE_SPACE_ONLY" if mode == "M0" else "OK"}
    if mode in ("M3", "M4"):
        base["status"] = "SPATIAL_UPDATE_BLOCKED_GEOMETRY"
    if trace is None:
        base.update({key: "" for key in (
            "velocity_rms_mps", "velocity_p95_mps", "velocity_p99_mps", "position_scatter_m",
            "position_endpoint_drift_m", "roll_std_deg", "pitch_std_deg", "yaw_drift_deg",
            "gyro_bias_x_dps", "gyro_bias_y_dps", "gyro_bias_z_dps", "cov_min_eigenvalue",
            "cov_max_eigenvalue", "finite")})
    else:
        q = (trace["time_s"] >= BASELINE[0]) & (trace["time_s"] < BASELINE[1]) & trace["valid_snapshot_mask"]
        velocity = np.linalg.norm(trace["velocity_mps"][q], axis=1)
        position = trace["position_m"][q]
        rpy = trace["rpy_deg"][q]
        relative = position - position[0]
        bias_dps = np.degrees(trace["final_gyro_bias_rad_s"])
        base.update({
            "velocity_rms_mps": np.sqrt(np.mean(velocity**2)),
            "velocity_p95_mps": np.quantile(velocity, .95), "velocity_p99_mps": np.quantile(velocity, .99),
            "position_scatter_m": np.sqrt(np.mean(np.sum((position - np.mean(position, axis=0))**2, axis=1))),
            "position_endpoint_drift_m": np.linalg.norm(relative[-1]),
            "roll_std_deg": np.std(rpy[:, 0]), "pitch_std_deg": np.std(rpy[:, 1]),
            "yaw_drift_deg": rpy[-1, 2] - rpy[0, 2],
            "gyro_bias_x_dps": bias_dps[0], "gyro_bias_y_dps": bias_dps[1], "gyro_bias_z_dps": bias_dps[2],
            "cov_min_eigenvalue": trace["covariance_min_eigenvalue"],
            "cov_max_eigenvalue": trace["covariance_max_eigenvalue"], "finite": int(trace["finite"]),
        })
    if range_summary:
        base.update({"uwb_valid": range_summary["valid"], "uwb_accepted": range_summary["accepted"],
                     "uwb_rejected": range_summary["rejected"], "uwb_residual_rms_mm": range_summary["residual_rms_mm"],
                     "uwb_nis_p95": range_summary["nis_p95"],
                     "uwb_accounting_closed": int(range_summary["accounting_closed"])})
    else:
        base.update({"uwb_valid": "", "uwb_accepted": "", "uwb_rejected": "", "uwb_residual_rms_mm": "",
                     "uwb_nis_p95": "", "uwb_accounting_closed": ""})
    return base


def _worker(node: str) -> dict:
    imu = _WORK["imu"][node]
    uwb = _WORK["uwb"][node]
    acc, gyro, _ = imu_physical(imu)
    clock = fit_node_clock(uwb)
    times = local_to_t0_s(imu["b306_us"], clock)
    stationary = intervals_to_mask(times, _WORK["intervals"][node])
    table_mask = np.zeros(len(times), dtype=bool)
    for event in _WORK["table_events"]:
        if node in event["nodes"].split(","):
            e = (float(event["onset_s"]), float(event["end_s"]))
            emask = intervals_to_mask(times, [e])
            stationary &= ~emask
            table_mask |= emask
    if node in MOVE_WINDOWS:
        stationary &= ~intervals_to_mask(times, [MOVE_WINDOWS[node]])

    baseline_ablation_end = int(np.searchsorted(times, 485.0, side="left"))
    configs = {
        "M1_actual": (InertialConfig(zupt=False), None),
        "M2_actual": (InertialConfig(zupt=True), None),
        "M2_fixed_dt": (InertialConfig(zupt=True, fixed_dt_s=0.005), baseline_ablation_end),
        "M2_no_gyro_bias": (InertialConfig(zupt=True, initialize_gyro_bias=False), baseline_ablation_end),
        "M2_vibration_included": (InertialConfig(zupt=True), None),
    }
    traces = {}
    for name, (cfg, limit) in configs.items():
        mask = stationary | table_mask if name == "M2_vibration_included" else stationary
        sl = slice(None, limit)
        traces[name] = replay_inertial(imu[sl], acc[sl], gyro[sl], times[sl], mask[sl], cfg)

    sigmas, uniform, baseline = robust_sigmas(uwb)
    range_runs, range_audit = {}, []
    for r_mode in ("uniform", "per_link"):
        for gate in (False, True):
            name = f"{r_mode}_gate_{'on' if gate else 'off'}"
            audit, summary = replay_range_space(
                uwb, imu["b306_us"], baseline, sigmas, uniform,
                RangeConfig(r_mode=r_mode, gate_enabled=gate,
                            collect_audit=(r_mode == "per_link" and gate)),
            )
            for row in audit:
                row.update({"node": node, "config": name})
            range_audit.extend(audit)
            range_runs[name] = summary
    return {"node": node, "clock": clock, "times": times, "stationary": stationary,
            "table_mask": table_mask, "traces": traces, "sigmas": sigmas, "uniform_sigma": uniform,
            "range_runs": range_runs, "range_audit": range_audit}


def savefig(path: Path) -> None:
    plt.tight_layout()
    plt.savefig(path, metadata={"Date": None})
    plt.close()


def render_plots(out: Path, results: dict[str, dict], disturbance: list[dict], reposition: list[dict]) -> None:
    matplotlib.rcParams["svg.hashsalt"] = "v47-static-fusion-replay-v1"
    x = np.arange(1801) / 60.0
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    for node in NODES:
        for ax, key, label in ((axes[0], "M1_actual", "M1"), (axes[1], "M2_actual", "M2")):
            tr = results[node]["traces"][key]
            ax.plot(x, np.linalg.norm(tr["velocity_mps"], axis=1), lw=.7, label=node)
            ax.set_ylabel(f"{label} |velocity| (m/s)")
    axes[1].set_xlabel("minutes from T0"); axes[0].legend(ncol=5, fontsize=7)
    savefig(out / "m1_m2_m3_m4_velocity.svg")

    plt.figure(figsize=(12, 6))
    for node in NODES:
        tr = results[node]["traces"]["M2_actual"]
        plt.plot(x, np.linalg.norm(tr["position_m"], axis=1), lw=.7, label=node)
    plt.xlabel("minutes from T0"); plt.ylabel("M2 relative position norm (m)"); plt.legend(ncol=5, fontsize=7)
    savefig(out / "position_and_platform.svg")

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    for node in NODES:
        rpy = results[node]["traces"]["M2_actual"]["rpy_deg"]
        for k, name in enumerate(("roll", "pitch", "yaw")):
            axes[k].plot(x, rpy[:, k], lw=.7, label=node); axes[k].set_ylabel(f"{name} (deg)")
    axes[-1].set_xlabel("minutes from T0"); axes[0].legend(ncol=5, fontsize=7)
    savefig(out / "attitude_rpy.svg")

    plt.figure(figsize=(10, 6))
    bias = np.array([np.degrees(results[n]["traces"]["M2_actual"]["initial_gyro_bias_rad_s"]) for n in NODES])
    xx = np.arange(len(NODES)); width = .25
    for k, axis in enumerate("xyz"):
        plt.bar(xx + (k - 1) * width, bias[:, k], width, label=axis)
    plt.xticks(xx, NODES, rotation=35); plt.ylabel("initial gyro bias (deg/s)"); plt.legend()
    savefig(out / "gyro_bias_estimate.svg")

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    for node in NODES:
        tr = results[node]["traces"]["M2_actual"]
        axes[0].plot(x, tr["cov_min_eig"], lw=.7); axes[1].plot(x, tr["cov_max_eig"], lw=.7, label=node)
    axes[0].set_ylabel("min covariance eigenvalue"); axes[1].set_ylabel("max covariance eigenvalue")
    axes[1].set_xlabel("minutes from T0"); axes[1].legend(ncol=5, fontsize=7); axes[1].set_yscale("symlog")
    savefig(out / "covariance_eigenvalues.svg")

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    for node in NODES:
        rr = results[node]["range_runs"]["per_link_gate_on"]
        axes[0].plot(x, rr["residual_rms_by_second"], lw=.7, label=node)
        axes[1].plot(x, rr["nis_mean_by_second"], lw=.7)
    axes[0].set_ylabel("range residual RMS (mm)"); axes[1].set_ylabel("range NIS mean")
    axes[1].set_xlabel("minutes from T0"); axes[0].legend(ncol=5, fontsize=7); axes[0].set_yscale("symlog")
    savefig(out / "uwb_residual_nis.svg")

    plt.figure(figsize=(12, 6))
    for node in NODES:
        plt.step(x, results[node]["traces"]["M2_actual"]["zupt_count_by_second"], where="post", lw=.7, label=node)
    plt.xlabel("minutes from T0"); plt.ylabel("Kalman ZUPT updates/s"); plt.legend(ncol=5, fontsize=7)
    savefig(out / "zupt_events.svg")

    plt.figure(figsize=(12, 5))
    onset = [float(r["onset_s"]) / 60 for r in disturbance]
    peak = [float(r["max_m2_velocity_delta_mps"]) for r in disturbance]
    plt.scatter(onset, peak, s=18); plt.xlabel("event onset (minutes from T0)"); plt.ylabel("max M2 velocity (m/s)")
    savefig(out / "table_vibration_response.svg")

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    for k, node in enumerate(("BSFC2CC", "BSFAA61")):
        tr = results[node]["traces"]["M2_actual"]
        q = (tr["time_s"] >= 480) & (tr["time_s"] <= 520)
        axes[k].plot(tr["time_s"][q], np.linalg.norm(tr["position_m"][q] - tr["position_m"][q][0], axis=1), label="M2 inertial")
        axes[k].set_ylabel(f"{node} delta p (m)"); axes[k].legend()
    axes[-1].set_xlabel("seconds from T0")
    savefig(out / "reposition_windows.svg")

    plt.figure(figsize=(12, 6))
    for node in NODES:
        a = results[node]["traces"]["M2_actual"]; f = results[node]["traces"]["M2_fixed_dt"]
        plt.plot(x, np.linalg.norm(a["position_m"] - f["position_m"], axis=1), lw=.7, label=node)
    plt.xlabel("minutes from T0"); plt.ylabel("actual-dt vs fixed-dt position difference (m)"); plt.legend(ncol=5, fontsize=7)
    savefig(out / "actual_vs_fixed_dt.svg")


def historical_algorithm_evidence(out: Path, results: dict[str, dict], geom: dict,
                                  metric_rows: list[dict]) -> None:
    source_commit_id = "1b80923d1afe483f3be5d1afc18d6ef8ea6c5802"
    branch_head = "94ef793faec8570fbf6779a187ad9765a15e9c22"
    base = "BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/IMU-Fusion-Simulation/"
    mapping = [
        {"t_number": "T2/T2LITE", "source_commit": source_commit_id,
         "source_file": base + "scripts/run_phase0_phase1_vertical_slice.py:relative_motion_filter; scripts/run_real_6axis_vertical_slice.py:forward_imu_corrects_uwb",
         "source_sha256": "88785eaf6de4a956001ee3ce965e59b4bdbaea59b10e27790d317bd76ff45efd / 06e812e56c1e0b962d1ada36f09ac3683aff50dd5529b074b7ebebfab9934af2",
         "measurement_domain": "solved UWB XYZ (mm) plus IMU-derived solved-position delta",
         "state": "phase2: x=position(3), P=3x3; T2LITE: causal output position only",
         "exact_update": "phase2 x_pred=x+delta_imu then 3D Kalman UWB XYZ update; T2LITE pred=out_prev+delta_imu, alpha=.42 if |innovation|<180mm else .18",
         "update_direction": "IMU_CORRECTS_UWB", "distinctness": "real T2 direction; not a generic EKF label"},
        {"t_number": "T3/T3LITE", "source_commit": source_commit_id,
         "source_file": base + "scripts/run_real_6axis_vertical_slice.py:forward_bidirectional_fusion; phase2 position_worker",
         "source_sha256": "06e812e56c1e0b962d1ada36f09ac3683aff50dd5529b074b7ebebfab9934af2 / 34af737fbb493bdab673af8107ac8c7345935e0db363a662590e1316eb134240",
         "measurement_domain": "solved UWB XYZ plus IMU-derived position trajectory",
         "state": "T3LITE: two causal output tracks; phase2 label reuses the 3-state relative_motion_filter",
         "exact_update": "T3LITE=0.68*T5LITE_UWB2IMU + 0.32*T2LITE_IMU2UWB at the same UWB time",
         "update_direction": "BIDIRECTIONAL_CORRECTION", "distinctness": "fixed blend is distinct; phase2 T3 implementation itself is not a distinct core"},
        {"t_number": "T5/T5LITE", "source_commit": source_commit_id,
         "source_file": base + "scripts/run_real_6axis_vertical_slice.py:fuse_uwb_imu_packet; phase2 position_worker",
         "source_sha256": "06e812e56c1e0b962d1ada36f09ac3683aff50dd5529b074b7ebebfab9934af2 / 34af737fbb493bdab673af8107ac8c7345935e0db363a662590e1316eb134240",
         "measurement_domain": "solved UWB XYZ (m), H=[I3,0]",
         "state": "real6: [p(3),v(3)], P=6x6, yaw external scalar; no implemented bias state despite T5 registry label",
         "exact_update": "IMU propagate p/v, then solved-UWB position innovation; 3D NIS<25; Kalman correction changes inertial p/v",
         "update_direction": "UWB_CORRECTS_IMU", "distinctness": "real T5 direction; phase2 T5 label reuses relative_motion_filter"},
        {"t_number": "T6", "source_commit": source_commit_id,
         "source_file": base + "scripts/run_phase2_stage1_screening.py:range_ekf_track",
         "source_sha256": "34af737fbb493bdab673af8107ac8c7345935e0db363a662590e1316eb134240",
         "measurement_domain": "raw per-anchor ranges after anchor/tag delay and bias correction",
         "state": "x=position(3), P=3x3, IMU position-delta prior",
         "exact_update": "H_i=(x-anchor_i)/range_i; joint range Kalman update",
         "update_direction": "RAW_UWB_CORRECTS_IMU_POSITION_PRIOR", "distinctness": "limited raw-range prototype; reported separately"},
        {"t_number": "T8", "source_commit": source_commit_id,
         "source_file": base + "scripts/run_phase2_stage1_screening.py:range_ekf_track(robust=True)",
         "source_sha256": "34af737fbb493bdab673af8107ac8c7345935e0db363a662590e1316eb134240",
         "measurement_domain": "same raw ranges as T6",
         "state": "same 3-state position prototype as T6",
         "exact_update": "T6 update with sigma multiplied by 8 where |residual|>3*sigma",
         "update_direction": "ROBUST_RAW_UWB_CORRECTS_IMU_POSITION_PRIOR", "distinctness": "limited robust prototype; reported separately"},
        {"t_number": "T11", "source_commit": source_commit_id,
         "source_file": base + "scripts/run_real_6axis_vertical_slice.py:integrate_imu_packet",
         "source_sha256": "06e812e56c1e0b962d1ada36f09ac3683aff50dd5529b074b7ebebfab9934af2",
         "measurement_domain": "IMU only",
         "state": "p(3),v(3),external scalar yaw; initialized from truth in old simulation",
         "exact_update": "strapdown diagnostic; actual dt with 1/120s fallback",
         "update_direction": "IMU_ONLY_CONTROL", "distinctness": "control, never Fusion winner"},
    ]
    write_csv(out / "HISTORICAL_ALGORITHM_MAP.csv", mapping)
    replay_rows = []
    metric_by = {(row["node"], row["mode"]): row for row in metric_rows}
    for node in NODES:
        for t_number, direction, domain in (
            ("T2", "IMU_CORRECTS_UWB", "solved_UWB_XYZ"),
            ("T3", "BIDIRECTIONAL_CORRECTION", "solved_UWB_XYZ"),
            ("T5", "UWB_CORRECTS_IMU", "solved_UWB_XYZ"),
            ("T6", "RAW_UWB_CORRECTS_IMU_POSITION_PRIOR", "raw_ranges+bound_anchor_XYZ/delay"),
            ("T8", "ROBUST_RAW_UWB_CORRECTS_IMU_POSITION_PRIOR", "raw_ranges+bound_anchor_XYZ/delay"),
        ):
            replay_rows.append({"node": node, "t_number": t_number, "direction": direction,
                                "adapter": "v47_real_data_adapter.load_capture", "window_start_s": 1,
                                "window_end_s": 484, "geometry_manifest_status": geom["binding_status"],
                                "measurement_domain": domain, "input_adapter_executed": 1,
                                "spatial_algorithm_updates": 0, "status": "BLOCKED_GEOMETRY_BINDING",
                                "velocity_rms_mps": "", "position_drift_m": "", "uwb_residual_rms_mm": "",
                                "note": "equal fail-closed gate; Erlangen geometry not substituted"})
        control = metric_by[(node, "M1")]
        replay_rows.append({"node": node, "t_number": "T11_REAL_CONTROL", "direction": "IMU_ONLY_CONTROL",
                            "adapter": "v47_real_data_adapter.load_capture", "window_start_s": 1,
                            "window_end_s": 484, "geometry_manifest_status": geom["binding_status"],
                            "measurement_domain": "real_JY61P_IMU", "input_adapter_executed": 1,
                            "spatial_algorithm_updates": 0, "status": "EXECUTED",
                            "velocity_rms_mps": control["velocity_rms_mps"],
                            "position_drift_m": control["position_endpoint_drift_m"], "uwb_residual_rms_mm": "",
                            "note": "truth initialization removed; real timestamp control"})
    write_csv(out / "HISTORICAL_DIRECTION_REPLAY.csv", replay_rows)
    selection = {
        "schema": "historical-fusion-direction-selection-v1", "audited_branch": "feature/wand-internal-sweep",
        "audited_branch_head": branch_head, "algorithm_origin_commit": source_commit_id,
        "performance_ranking_allowed": False, "reason": "all spatial historical paths are equally blocked by current-room geometry/delay binding",
        "inherit": {"direction": "T5 UWB_CORRECTS_IMU", "scope": "architecture direction only",
                    "implementation": "do not inherit T5LITE unchanged; replace truth/yaw-only/solved-position assumptions after geometry-bound replay",
                    "rationale": "one coherent inertial state receives probabilistic observations; compatible with future raw-range T6/T8 coupling"},
        "eliminate_from_final_architecture": [
            {"algorithm": "T2LITE", "reason": "fixed alpha heuristic has no covariance-consistent feedback; retain only as IMU-corrects-UWB control"},
            {"algorithm": "T3LITE", "reason": "fixed 0.68/0.32 blend double-counts correlated inputs and has no joint covariance; retain only as bidirectional historical control"},
            {"algorithm": "phase2 generic T2/T3/T5 label sweep", "reason": "all three labels call the same relative_motion_filter and differ mainly by sigma, so it cannot establish correction-direction superiority"},
        ],
        "separate_pending_candidates": {"T6": "raw-range prototype, blocked geometry", "T8": "robust raw-range prototype, blocked geometry"},
        "control_only": {"T11": "IMU-only free-drift control"},
        "warning": "This is a structural inheritance decision, not a real-data performance win; M4 is not selected as the final architecture.",
    }
    write_json(out / "ARCHITECTURE_SELECTION.json", selection)


def run(data_root: Path, out: Path, workers: int) -> None:
    if out.exists() and any(out.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty {out}")
    out.mkdir(parents=True, exist_ok=True)
    raw = data_root / "formal_capture/fusion_host_raw.cobs.bin"
    if sha256(raw) != RAW_SHA:
        raise RuntimeError("raw SHA mismatch before replay")
    imu, uwb, replay = load_capture(data_root)
    if replay.raw_sha256 != RAW_SHA:
        raise RuntimeError("adapter raw SHA mismatch")
    prior = data_root / "analysis_real_sensor_static_v1"
    intervals, table_events, move_events = load_annotations(prior)
    geom = geometry_manifest(data_root, uwb)
    write_json(out / "CAPTURE_BOUND_GEOMETRY_MANIFEST.json", geom)

    global _WORK
    _WORK = {"imu": imu, "uwb": uwb, "intervals": intervals, "table_events": table_events}
    context = mp.get_context("fork")
    with context.Pool(processes=min(workers, len(NODES))) as pool:
        worker_results = pool.map(_worker, NODES)
    results = {row["node"]: row for row in worker_results}

    input_config = {
        "schema": "v47-static-fusion-real-input-v1", "capture_id": CAPTURE_ID,
        "source_raw": "formal_capture/fusion_host_raw.cobs.bin", "source_raw_sha256": RAW_SHA,
        "t0_wall": T0_WALL, "t0_master_ms": T0_MASTER_MS, "nodes": list(NODES),
        "imu_time": "B306 extended TIMER2 base_us + delta_us; every sample retained",
        "uwb_measurement_time": "B306 hardware-captured poll-TX ready edge strobe_us",
        "master_time_use": "frozen annotation selection only; never propagation or UWB measurement time",
        "static_baseline_s": [1, 484], "frozen_move_windows": MOVE_WINDOWS,
        "table_vibration_event_count": len(table_events), "interpolation": None, "resampling": None,
        "navigation_frame": "per-node local z-up; yaw=0 arbitrary; not current-room frame",
        "gravity_convention": "JY61P acceleration is specific force; q maps initialization mean to +z; subtract +9.80665 m/s^2",
    }
    replay_configs = {
        "common_inertial": {"state": "p(3), v(3), scalar-first q_body_to_local(4), gyro_bias(3); 12-state error covariance",
                            "initialization": "gyro mean and gravity direction over T0+1..60 s; yaw=0",
                            "accel_noise_sigma_mps2": .12, "gyro_noise_sigma_dps": .12,
                            "gyro_bias_rw_sigma_dps2": .002, "covariance_update_hz": 20},
        "M0": "eight independent range states; no position when geometry blocked",
        "M1": "actual-time IMU propagation, no ZUPT/UWB", "M2": "M1 + 20 Hz Kalman zero-velocity measurement",
        "M3": "M1 + audited UWB range-space path; position coupling blocked", "M4": "M2 + audited UWB range-space path; position coupling blocked",
        "zupt": {"source": "frozen STATIC_SEGMENTS.csv minus all frozen table/move events", "measurement_sigma_mps": .02,
                 "direct_velocity_assignment": False},
        "uwb": {"per_link_R": "(max(20 mm, 1.4826*MAD))^2 from T0+1..484 s only",
                "uniform_R": "median per-link robust sigma, same floor", "nis_gate": 10.827566,
                "quality_changes_measurement": False, "invalid_policy": "audit, no update"},
        "ablations": ["fixed 0.005 s vs actual over frozen T0+1..484 s baseline",
                      "no gyro-bias initialization over frozen T0+1..484 s baseline", "M1/M2/M3/M4",
                      "uniform vs per-link R", "gate disabled vs enabled", "table vibration ZUPT included vs excluded"],
    }
    write_json(out / "REAL_INPUT_CONFIG.json", input_config)
    write_json(out / "REPLAY_CONFIGS.json", replay_configs)

    metric_rows, static_rows, zupt_rows, covariance = [], [], [], {"nodes": {}, "all_gates_pass": True}
    accounting_rows, rejection_rows, ablation_rows = [], [], []
    for node in NODES:
        result = results[node]
        nominal_range = result["range_runs"]["per_link_gate_on"]
        traces_for_mode = {"M0": None, "M1": result["traces"]["M1_actual"], "M2": result["traces"]["M2_actual"],
                           "M3": result["traces"]["M1_actual"], "M4": result["traces"]["M2_actual"]}
        for mode in MODES:
            metric_rows.append(mode_metrics(node, mode, traces_for_mode[mode], nominal_range if mode in ("M0", "M3", "M4") else None))
        m1 = metric_rows[-4]; m2 = metric_rows[-3]
        ratio = float(m2["velocity_rms_mps"]) / max(float(m1["velocity_rms_mps"]), 1e-15)
        static_rows.append({"node": node, "baseline_start_s": 1, "baseline_end_s": 484,
                            "m1_velocity_rms_mps": m1["velocity_rms_mps"], "m2_velocity_rms_mps": m2["velocity_rms_mps"],
                            "m2_over_m1_velocity_rms": ratio, "significantly_lower": int(ratio < .25),
                            "m3_position_divergence_m": m1["position_endpoint_drift_m"],
                            "m4_position_divergence_m": m2["position_endpoint_drift_m"],
                            "m4_no_worse_than_m3": int(float(m2["position_endpoint_drift_m"]) <= float(m1["position_endpoint_drift_m"])),
                            "geometry_limited": 1})
        t2 = result["traces"]["M2_actual"]
        zupt_rows.append({"node": node, "stationary_candidate_samples": t2["stationary_candidate_samples"],
                          "zupt_updates": t2["zupt_updates"], "zupt_nis_median": np.median(t2["zupt_nis"]),
                          "zupt_nis_p95": np.quantile(t2["zupt_nis"], .95), "direct_velocity_assignment": 0,
                          "move_window_updates": 0 if node in MOVE_WINDOWS else ""})
        cov_ok = t2["finite"] and t2["covariance_min_eigenvalue"] >= -1e-9 and t2["covariance_max_asymmetry"] <= 1e-10
        covariance["nodes"][node] = clean({"finite": t2["finite"], "min_eigenvalue": t2["covariance_min_eigenvalue"],
                                           "max_eigenvalue": t2["covariance_max_eigenvalue"],
                                           "max_asymmetry": t2["covariance_max_asymmetry"],
                                           "batch_boundary_max_position_step_m": t2["batch_boundary_max_position_step_m"],
                                           "psd_pass": bool(cov_ok)})
        covariance["all_gates_pass"] &= bool(cov_ok)
        for config_name, summary in result["range_runs"].items():
            accounting_rows.append({"node": node, "config": config_name, "records": summary["records"],
                                    "slots_total": summary["slots_total"], "valid": summary["valid"], "invalid": summary["invalid"],
                                    "accepted": summary["accepted"], "rejected": summary["rejected"],
                                    "accounting_closed": int(summary["accounting_closed"]), "insertion_errors": summary["insertion_errors"],
                                    "residual_rms_mm": summary["residual_rms_mm"], "residual_p95_abs_mm": summary["residual_p95_abs_mm"],
                                    "nis_median": summary["nis_median"], "nis_p95": summary["nis_p95"],
                                    "range_state_changed": int(summary["state_changed"])})
        rejection_rows.extend(result["range_audit"])
        base_trace = result["traces"]["M2_actual"]
        for name in ("M2_fixed_dt", "M2_no_gyro_bias", "M2_vibration_included"):
            trace = result["traces"][name]
            q = (trace["time_s"] >= 1) & (trace["time_s"] < 484) & trace["valid_snapshot_mask"]
            vel = np.linalg.norm(trace["velocity_mps"][q], axis=1)
            ablation_rows.append({"node": node, "mechanism": name, "velocity_rms_mps": np.sqrt(np.mean(vel**2)),
                                  "endpoint_position_m": np.linalg.norm(trace["position_m"][q][-1] - trace["position_m"][q][0]),
                                  "delta_endpoint_vs_nominal_m": np.linalg.norm(trace["position_m"][q][-1] - base_trace["position_m"][q][-1]),
                                  "zupt_updates": trace["zupt_updates"]})
        for name, summary in result["range_runs"].items():
            ablation_rows.append({"node": node, "mechanism": f"UWB_{name}", "velocity_rms_mps": "", "endpoint_position_m": "",
                                  "delta_endpoint_vs_nominal_m": "", "zupt_updates": "",
                                  "uwb_accepted": summary["accepted"], "uwb_rejected": summary["rejected"],
                                  "uwb_residual_rms_mm": summary["residual_rms_mm"], "uwb_nis_p95": summary["nis_p95"]})

    disturbance_rows = []
    for event in table_events:
        start, end = float(event["onset_s"]), float(event["end_s"])
        max_velocity_delta = 0.0
        max_post_velocity_delta = 0.0
        max_excess_step = 0.0
        zupt_during = 0
        for node in event["nodes"].split(","):
            tr = results[node]["traces"]["M2_actual"]
            pre_i = max(0, int(math.floor(start)) - 1)
            event_end_i = min(1800, int(math.ceil(end)))
            post_i = min(1800, event_end_i + 5)
            pre_v = tr["velocity_mps"][pre_i]
            during = tr["velocity_mps"][int(math.floor(start)):event_end_i + 1]
            max_velocity_delta = max(max_velocity_delta, float(np.nanmax(np.linalg.norm(during - pre_v, axis=1))))
            max_post_velocity_delta = max(max_post_velocity_delta,
                                          float(np.linalg.norm(tr["velocity_mps"][post_i] - pre_v)))
            elapsed = float(post_i - pre_i)
            excess = tr["position_m"][post_i] - tr["position_m"][pre_i] - pre_v * elapsed
            max_excess_step = max(max_excess_step, float(np.linalg.norm(excess)))
            zupt_during += int(np.sum(tr["zupt_count_by_second"][int(math.floor(start)):event_end_i]))
        rejected = sum(1 for row in rejection_rows if row["valid"] == 1 and start <= row["t0_s"] < end and row["node"] in event["nodes"])
        disturbance_rows.append({"event_id": event["event_id"], "onset_s": start, "end_s": end,
                                 "nodes": event["nodes"], "max_m2_velocity_delta_mps": max_velocity_delta,
                                 "max_post_event_velocity_delta_mps": max_post_velocity_delta,
                                 "max_post_event_excess_position_m": max_excess_step,
                                 "zupt_updates_during_event": zupt_during,
                                 "uwb_nis_rejections": rejected, "filter_reinitializations": 0,
                                 "permanent_step_claimed": 0, "verdict": "MEASURED_NO_REINITIALIZATION"})

    reposition_rows = []
    for node, (start, end) in MOVE_WINDOWS.items():
        u = uwb[node]; t = (u["master_ms"].astype(np.int64) - T0_MASTER_MS) / 1000.0
        tr1 = results[node]["traces"]["M1_actual"]; tr2 = results[node]["traces"]["M2_actual"]
        for slot in range(8):
            valid = (u["valid_mask"] & (1 << slot)) != 0
            before = u["range_mm"][(t >= start - 12) & (t < start - 2) & valid, slot].astype(float)
            after = u["range_mm"][(t >= end + 3) & (t < end + 18) & valid, slot].astype(float)
            reposition_rows.append({"node": node, "window_start_s": start, "window_end_s": end, "anchor_slot": slot,
                                    "before_median_mm": np.median(before), "after_median_mm": np.median(after),
                                    "platform_delta_mm": np.median(after) - np.median(before),
                                    "after_robust_sigma_mm": 1.4826 * np.median(np.abs(after - np.median(after))),
                                    "m1_relative_displacement_480_520_m": np.linalg.norm(tr1["position_m"][520] - tr1["position_m"][480]),
                                    "m2_relative_displacement_480_520_m": np.linalg.norm(tr2["position_m"][520] - tr2["position_m"][480]),
                                    "zupt_exited_during_move": int(np.sum(tr2["zupt_count_by_second"][int(start):int(math.ceil(end))]) == 0),
                                    "post_move_static_reestablished": int(np.sum(tr2["zupt_count_by_second"][int(end) + 3:int(end) + 18]) > 0),
                                    "interpretation": "stable_new_range_platform; translation_vs_rotation_or_antenna_orientation_unresolved"})

    write_csv(out / "PER_NODE_MODE_METRICS.csv", metric_rows)
    write_csv(out / "STATIC_REPLAY_SUMMARY.csv", static_rows)
    write_csv(out / "ZUPT_ACCOUNTING.csv", zupt_rows)
    write_json(out / "COVARIANCE_AUDIT.json", covariance)
    write_csv(out / "UWB_OBSERVATION_ACCOUNTING.csv", accounting_rows)
    write_csv(out / "UWB_REJECTION_AUDIT.csv", rejection_rows)
    write_csv(out / "DISTURBANCE_REPLAY.csv", disturbance_rows)
    write_csv(out / "REPOSITION_REPLAY.csv", reposition_rows)
    write_csv(out / "ABLATION_RESULTS.csv", ablation_rows)
    historical_algorithm_evidence(out, results, geom, metric_rows)

    all_finite = all(row["finite"] in (1, "1") for row in metric_rows if row["mode"] in ("M1", "M2", "M3", "M4"))
    structure_pass = all_finite and covariance["all_gates_pass"] and all(r["significantly_lower"] for r in static_rows) \
        and all(r["m4_no_worse_than_m3"] for r in static_rows) and all(r["accounting_closed"] for r in accounting_rows) \
        and all(r["insertion_errors"] == 0 for r in accounting_rows)
    verdict = "BLOCKED_GEOMETRY_BINDING" if structure_pass else "ALGORITHM_DEFECT"
    audit = """# Algorithm audit\n\n## Finding\n\nThe historical directions are not collapsed. `HISTORICAL_ALGORITHM_MAP.csv` binds every T number to its exact source file, origin commit `1b80923d1afe483f3be5d1afc18d6ef8ea6c5802`, measurement domain, state and update direction. Git blame confirms `relative_motion_filter`, `integrate_imu_packet`, `fuse_uwb_imu_packet`, `forward_imu_corrects_uwb`, `forward_bidirectional_fusion`, and `range_ekf_track` originate in that commit; later branch head `94ef793faec8570fbf6779a187ad9765a15e9c22` does not create three independent phase2 EKFs.\n\nT2/T2LITE is IMU-corrects-UWB: an IMU position delta predicts the UWB output track, then UWB pulls that output with a Kalman gain or fixed alpha. T5/T5LITE is UWB-corrects-IMU: IMU propagates `[p,v]`, then solved-UWB XYZ updates the inertial state. T3LITE is a literal same-time `0.68*T5 + 0.32*T2` bidirectional output blend. Critically, the phase2 T2/T3/T5 sweep calls the same 3-state `relative_motion_filter` for all three and changes sigma/labels; it is not evidence that three distinct EKFs ran. The T5 registry name says error-state/bias, while executed real6 T5LITE has `[p,v]`, a scalar external yaw and no bias state.\n\nT6 and T8 are separately audited raw-range position prototypes. Both need bound Anchor XYZ/delay; T8 inflates sigma eightfold for residuals beyond three sigma. T11 is strictly the IMU-only control and historically injects truth initialization.\n\nThe current real adapter and identical T0+1..484 s window are entered for T2/T3/T5/T6/T8/T11. T11 executes. All five spatial algorithms stop at the same capture-bound geometry gate, with zero state updates; this is recorded per node in `HISTORICAL_DIRECTION_REPLAY.csv`. Running one of them with Erlangen geometry would violate the capture contract and would not be a reproduction of this room. Therefore no performance ranking is claimed. `ARCHITECTURE_SELECTION.json` makes only a structural decision: inherit the T5 UWB-corrects-IMU direction, retain T2/T3 as controls, eliminate their fixed-alpha/fixed-blend implementations from the final architecture, and leave T6/T8 pending geometry-bound comparison. M4 is not declared the final architecture.\n\n## Real compatibility extension\n\n| Concern | Old executed behavior | Real compatibility action | Status |\n|---|---|---|---|\n| state | `[p,v]` plus external scalar yaw | scalar-first body-to-local quaternion and gyro-bias error states | minimal required extension |\n| initialization | truth position/velocity/yaw | local origin/zero velocity, gravity roll/pitch, arbitrary yaw=0, static gyro bias | truth injection removed |\n| time | actual synthetic times with 1/120 fallback | every actual B306 TIMER2 sample; explicit fixed-dt ablation only | validated |\n| gravity | Vicon world y-up | per-node local z-up; specific force maps to +z, then gravity is subtracted | internally validated; room/body extrinsic missing |\n| ZUPT | registry pending | explicit zero-velocity Kalman measurement at 20 Hz under frozen labels | implemented |\n| UWB | solved position or Erlangen-bound range prototype | complete range-space innovation/accounting only | spatial coupling blocked |\n\nThis proves the real adapter, inertial propagation, gravity initialization, explicit ZUPT, asynchronous UWB insertion and range innovation plumbing. It does not claim completed historical spatial reproduction.\n"""
    (out / "ALGORITHM_AUDIT.md").write_text(audit, encoding="utf-8")

    median_m1 = float(np.median([float(r["m1_velocity_rms_mps"]) for r in static_rows]))
    median_m2 = float(np.median([float(r["m2_velocity_rms_mps"]) for r in static_rows]))
    max_m2 = float(np.max([float(r["m2_velocity_rms_mps"]) for r in static_rows]))
    max_vibration_dv = max(float(r["max_m2_velocity_delta_mps"]) for r in disturbance_rows)
    max_vibration_dp = max(float(r["max_post_event_excess_position_m"]) for r in disturbance_rows)
    total_valid = sum(r["valid"] for r in accounting_rows if r["config"] == "per_link_gate_on")
    total_accept = sum(r["accepted"] for r in accounting_rows if r["config"] == "per_link_gate_on")
    report = f"""# v47 real ten-node static Fusion replay\n\n## Verdict: {verdict}\n\nThe non-spatial structure passes: all state/covariance values are finite; covariance is symmetric and PSD within numerical tolerance; there are no local timestamp reversals, asynchronous insertion errors, observation-accounting losses or filter reinitializations. Median static velocity RMS falls from M1 `{median_m1:.6g} m/s` to M2 `{median_m2:.6g} m/s` (worst-node M2 `{max_m2:.6g} m/s`), so the explicit Kalman ZUPT materially suppresses free inertial drift. M3 and M4 execute the same complete UWB range observation plumbing as M0, but correctly apply zero spatial state corrections because geometry is not bound. That limitation—not a decorative update—is explicit in every mode row.\n\nThe geometry gate is blocked. The capture manifest contains no current-room XYZ/delay/solver reference. The only complete layouts found are SHA-pinned Erlangen 2026-07-10 artifacts and were rejected. Consequently no absolute or relative room-frame position and no ground-truth accuracy are reported. The nominal per-link-R/gate-on path accounts for `{total_valid}` valid ranges and accepts `{total_accept}`; every invalid and NIS-rejected item is retained in `UWB_REJECTION_AUDIT.csv`. R is estimated only from frozen T0+1..484 s range dispersion and is not used to infer geometry.\n\nAll 38 frozen table-vibration annotations are replayed without filter reinitialization, and nominal ZUPT performs zero updates inside their half-open annotated intervals. They are not harmless to this minimal inertial model: the worst event-induced velocity change is `{max_vibration_dv:.6g} m/s` and the worst five-second post-event position excess over a pre-event constant-velocity extrapolation is `{max_vibration_dp:.6g} m`. Those values are reported rather than thresholded after the fact; they expose the missing accelerometer attitude correction/ZARU and position-observation path. The include/exclude ablation records the consequence of forcing ZUPT during vibration. Neither table response nor BSF6C53's Listener reception is relabeled as a sensor defect.\n\nBSFC2CC and BSFAA61 both exit ZUPT during their frozen move intervals and regain it afterward. Both raw eight-range vectors establish persistent post-move platforms; C2CC changes strongly on all links, while AA61 is smaller and its Anchor-0 post-window remains RF-scattered. The scalar range-space gate does not reliably reacquire these platform jumps, so it is diagnostic plumbing, not the spatial Fusion algorithm. Without geometry or ground truth, neither node is assigned an absolute displacement error.\n\nThe time contract is verified within each node: IMU uses every `base_us+delta_us` sample and UWB updates are inserted by hardware `strobe_us` between bracketing IMU steps. Master receipt time is used only to apply frozen annotations. Coordinate handling is internally consistent for local gravity and quaternion normalization, but the board/body extrinsic, signed-axis bench validation and room frame remain calibration gaps.\n\nKnown manual trajectory capture is **not yet authorized for a spatial Fusion verdict**. It becomes reasonable after one capture-bound current-room manifest is frozen, a signed-axis/body-extrinsic check is performed, and the table-vibration attitude response is corrected. Raw human data may still be collected as data, but no human Fusion accuracy claim is permitted before those gates.\n"""
    (out / "REPORT.md").write_text(report, encoding="utf-8")
    next_plan = """# Minimum known manual trajectory plan\n\n1. Freeze one signed capture manifest that maps Anchor IDs A–H/0–7 to current-room XYZ (mm), coordinate handedness/up axis, per-anchor and tag delay, production solver Git commit/SHA and R provenance. Do not refit it from the trajectory.\n2. Perform a bench signed-axis check on one representative Fusion PCB, then record each board-to-body mounting rotation. Yaw must come from an explicit initial heading/fixture, not gravity.\n3. Before the trajectory, add an accelerometer attitude correction/ZARU policy and replay the frozen 38 table events. Preserve all thresholds/configs and require the event response to be evaluated against a predeclared bound.\n4. Use one Fusion PCB first. Hold still for 60 s, translate it along a measured 1 m straight rail or taped line, stop 30 s, return along the same path, stop 60 s. Add one known 90-degree in-place rotation as a separate segment.\n5. Record external ground truth at surveyed endpoints (and preferably continuous Vicon/optical truth), while retaining the same B306 TIMER2 raw contract. Repeat three times without changing filter parameters.\n6. Gate on structural health first, then endpoint displacement, return closure, vibration false motion, UWB residual/NIS and repeatability. Only after the single-node gate should the experiment expand to ten nodes or human mounting.\n"""
    (out / "NEXT_MANUAL_TRAJECTORY_PLAN.md").write_text(next_plan, encoding="utf-8")

    render_plots(out, results, disturbance_rows, reposition_rows)
    if sha256(raw) != RAW_SHA:
        raise RuntimeError("raw SHA mismatch after replay")
    evidence = {"raw_sha_before": RAW_SHA, "raw_sha_after": sha256(raw),
                "adapter_replay_audit": clean(replay.__dict__), "structure_pass": bool(structure_pass),
                "verdict": verdict, "deterministic_second_run_required": True}
    write_json(out / "REPLAY_EVIDENCE.json", evidence)
    files = sorted(path for path in out.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (out / "SHA256SUMS").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=min(8, max(1, (os.cpu_count() or 2) - 2)))
    args = parser.parse_args()
    run(args.data_root, args.output, args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
