#!/usr/bin/env python3
"""Real 6-axis pseudo-IMU vertical slice.

This is intentionally separate from the older Phase 1/2/3 drift-prior runners.
It generates accel/gyro packets from Opti/Vicon trajectory samples, adds a
datasheet-backed sensor model, and compares:

* pure UWB baseline
* perfect IMU-only dead reckoning
* realistic IMU-only dead reckoning
* causal UWB + simulated 6-axis IMU fusion

The exported row is a smoke/vertical-slice result, not the final Phase 4 FULL
algorithm claim.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

SIM_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SIM_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import run_phase0_phase1_vertical_slice as P1  # noqa: E402

G_MPS2 = 9.80665
G_WORLD = np.array([0.0, -G_MPS2, 0.0], dtype=float)
TAGS = ["BS2DCE", "BSDC91"]

I_MODS = {
    "I0": {"bias": 1.0, "noise": 1.0, "rw": 1.0, "vib": 1.0, "lowpass": 1},
    "I1": {"bias": 1.0, "noise": 0.55, "rw": 1.0, "vib": 0.75, "lowpass": 7},
    "I3": {"bias": 0.40, "noise": 1.0, "rw": 0.85, "vib": 1.0, "lowpass": 1},
    "I1+I3": {"bias": 0.35, "noise": 0.55, "rw": 0.75, "vib": 0.75, "lowpass": 7},
    "I1+I3+I7": {"bias": 0.28, "noise": 0.45, "rw": 0.65, "vib": 0.35, "lowpass": 9},
    "I1+I2+I3+I8": {"bias": 0.22, "noise": 0.40, "rw": 0.55, "vib": 0.40, "lowpass": 11},
}


def stable_seed(*parts: object) -> int:
    h = hashlib.sha256("::".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "little") & 0xFFFFFFFF


def utc_run_id() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def ensure_dirs(run_dir: Path) -> None:
    for rel in ["tables", "traces", "figs/contact_sheets", "reports", "manifests"]:
        (run_dir / rel).mkdir(parents=True, exist_ok=True)


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def moving_average(arr: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or arr.shape[0] < 3:
        return arr.copy()
    window = int(max(1, window))
    if window % 2 == 0:
        window += 1
    pad = window // 2
    out = np.empty_like(arr, dtype=float)
    for axis in range(arr.shape[1]):
        vals = arr[:, axis]
        padded = np.pad(vals, (pad, pad), mode="edge")
        kernel = np.ones(window, dtype=float) / float(window)
        out[:, axis] = np.convolve(padded, kernel, mode="valid")
    return out


def interp_columns(t_src: np.ndarray, xyz_src: np.ndarray, t_query: np.ndarray) -> np.ndarray:
    out = np.full((len(t_query), xyz_src.shape[1]), np.nan, dtype=float)
    good = np.isfinite(t_src) & np.isfinite(xyz_src).all(axis=1)
    if int(np.sum(good)) < 2:
        return out
    order = np.argsort(t_src[good])
    ts = t_src[good][order]
    xs = xyz_src[good][order]
    keep = np.concatenate([[True], np.diff(ts) > 1e-9])
    ts = ts[keep]
    xs = xs[keep]
    if len(ts) < 2:
        return out
    for axis in range(xyz_src.shape[1]):
        out[:, axis] = np.interp(t_query, ts, xs[:, axis], left=np.nan, right=np.nan)
    return out


def normalize_rows(v: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    out = np.asarray(v, dtype=float).copy()
    prev = np.asarray(fallback, dtype=float)
    for i in range(out.shape[0]):
        n = float(np.linalg.norm(out[i]))
        if not math.isfinite(n) or n < 1e-9:
            out[i] = prev
        else:
            out[i] = out[i] / n
            prev = out[i]
    return out


def rotation_from_forward(forward: np.ndarray) -> np.ndarray:
    up = np.array([0.0, 1.0, 0.0], dtype=float)
    right = np.cross(forward, up)
    rn = float(np.linalg.norm(right))
    if rn < 1e-9 or not math.isfinite(rn):
        right = np.array([0.0, 0.0, 1.0], dtype=float)
    else:
        right = right / rn
    return np.column_stack([forward, up, right])


def rotations_from_velocity(vel_mps: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    horizontal = vel_mps.copy()
    horizontal[:, 1] = 0.0
    forward = normalize_rows(horizontal, np.array([1.0, 0.0, 0.0], dtype=float))
    yaw = np.unwrap(np.arctan2(forward[:, 2], forward[:, 0]))
    return forward, yaw


def load_sensors() -> dict:
    return yaml.safe_load((SIM_ROOT / "configs" / "sensors.yaml").read_text(encoding="utf-8"))


def sensor_value(sensor: dict, key: str, default: float = 0.0) -> float:
    val = sensor.get(key, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def build_nominal_packet(track: pd.DataFrame, odr_hz: float, orientation_mode: str) -> pd.DataFrame:
    g = track.sort_values("time_s").copy()
    t_samples = g["time_s"].to_numpy(float)
    truth_mm = g[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float)
    t0 = float(np.nanmin(t_samples))
    t1 = float(np.nanmax(t_samples))
    dt_s = 1.0 / odr_hz
    t = np.arange(t0, t1 + 0.5 * dt_s, dt_s, dtype=float)
    xyz_mm = interp_columns(t_samples, truth_mm, t)
    good = np.isfinite(xyz_mm).all(axis=1)
    if int(np.sum(good)) < 8:
        raise RuntimeError("insufficient truth samples for IMU packet")
    xyz_mm = pd.DataFrame(xyz_mm).interpolate(limit_direction="both").to_numpy(float)
    xyz_mm = moving_average(xyz_mm, 9)
    pos_m = xyz_mm / 1000.0
    # Build a discrete acceleration sequence that is self-consistent with the
    # exact integration equation used below. A plain second derivative looks
    # plausible as a signal, but it drifts when re-integrated and makes the
    # "perfect IMU" control fail for numerical reasons.
    vel_mps = np.zeros_like(pos_m)
    acc_mps2 = np.zeros_like(pos_m)
    if len(pos_m) > 1:
        vel_mps[0] = (pos_m[1] - pos_m[0]) / dt_s
    for i in range(len(pos_m) - 1):
        acc_mps2[i] = 2.0 * (pos_m[i + 1] - pos_m[i] - vel_mps[i] * dt_s) / (dt_s * dt_s)
        vel_mps[i + 1] = vel_mps[i] + acc_mps2[i] * dt_s
    if len(pos_m) > 1:
        acc_mps2[-1] = acc_mps2[-2]
    if orientation_mode == "trajectory_yaw":
        forward, yaw = rotations_from_velocity(vel_mps)
        yaw_rate = np.gradient(yaw, dt_s, edge_order=1)
        accel_body = np.empty_like(acc_mps2)
        for i in range(len(t)):
            rot_wb = rotation_from_forward(forward[i])
            accel_body[i] = rot_wb.T @ (acc_mps2[i] - G_WORLD)
    elif orientation_mode == "world_aligned":
        forward = np.tile(np.array([1.0, 0.0, 0.0], dtype=float), (len(t), 1))
        yaw = np.zeros(len(t), dtype=float)
        yaw_rate = np.zeros(len(t), dtype=float)
        accel_body = acc_mps2 - G_WORLD
    else:
        raise ValueError(f"unsupported orientation_mode={orientation_mode}")
    gyro_body = np.zeros_like(accel_body)
    gyro_body[:, 1] = yaw_rate
    return pd.DataFrame(
        {
            "imu_time_s": t,
            "truth_x_mm": xyz_mm[:, 0],
            "truth_y_mm": xyz_mm[:, 1],
            "truth_z_mm": xyz_mm[:, 2],
            "truth_vx_mps": vel_mps[:, 0],
            "truth_vy_mps": vel_mps[:, 1],
            "truth_vz_mps": vel_mps[:, 2],
            "truth_yaw_rad": yaw,
            "forward_x": forward[:, 0],
            "forward_y": forward[:, 1],
            "forward_z": forward[:, 2],
            "accel_x_mps2": accel_body[:, 0],
            "accel_y_mps2": accel_body[:, 1],
            "accel_z_mps2": accel_body[:, 2],
            "gyro_x_rad_s": gyro_body[:, 0],
            "gyro_y_rad_s": gyro_body[:, 1],
            "gyro_z_rad_s": gyro_body[:, 2],
        }
    )


def apply_sensor_model(
    nominal: pd.DataFrame,
    sensor: dict,
    i_id: str,
    run_id: str,
    capture_id: str,
    tag: str,
) -> pd.DataFrame:
    mod = I_MODS[i_id]
    rng = np.random.default_rng(stable_seed(run_id, "real6axis", sensor.get("name"), i_id, capture_id, tag))
    out = nominal.copy()
    dt_med = float(np.nanmedian(np.diff(out["imu_time_s"].to_numpy(float))))
    if not math.isfinite(dt_med) or dt_med <= 0:
        dt_med = 1.0 / sensor_value(sensor, "odr_hz", 120.0)

    accel = out[["accel_x_mps2", "accel_y_mps2", "accel_z_mps2"]].to_numpy(float)
    gyro = out[["gyro_x_rad_s", "gyro_y_rad_s", "gyro_z_rad_s"]].to_numpy(float)

    accel_bias_sigma = sensor_value(sensor, "residual_accel_bias_mg") * mod["bias"] * G_MPS2 / 1000.0
    gyro_bias_sigma = math.radians(sensor_value(sensor, "residual_gyro_bias_dps") * mod["bias"])
    accel_rw = sensor_value(sensor, "accel_bias_random_walk_mg_sqrt_s") * mod["rw"] * G_MPS2 / 1000.0
    gyro_rw = math.radians(sensor_value(sensor, "gyro_bias_random_walk_dps_sqrt_s") * mod["rw"])
    accel_noise = sensor_value(sensor, "accel_noise_mg") * mod["noise"] * G_MPS2 / 1000.0
    gyro_noise = math.radians(sensor_value(sensor, "gyro_noise_dps") * mod["noise"])
    vib_amp = sensor_value(sensor, "vibration_sensitivity_mg") * mod["vib"] * G_MPS2 / 1000.0
    extrinsic_accel = sensor_value(sensor, "extrinsic_mg") * G_MPS2 / 1000.0 * rng.normal(0.0, 1.0, size=3)

    accel_bias = rng.normal(0.0, accel_bias_sigma, size=3) + extrinsic_accel
    gyro_bias = rng.normal(0.0, gyro_bias_sigma, size=3)
    phase = rng.uniform(0.0, 2.0 * math.pi, size=3)
    freq = rng.uniform(6.0, 15.0, size=3)
    accel_meas = np.empty_like(accel)
    gyro_meas = np.empty_like(gyro)
    times = out["imu_time_s"].to_numpy(float)
    for i, t in enumerate(times):
        if i > 0:
            dti = float(times[i] - times[i - 1])
            if not math.isfinite(dti) or dti <= 0:
                dti = dt_med
            accel_bias += rng.normal(0.0, accel_rw * math.sqrt(dti), size=3)
            gyro_bias += rng.normal(0.0, gyro_rw * math.sqrt(dti), size=3)
        vib = vib_amp * np.sin(2.0 * math.pi * freq * t + phase)
        accel_meas[i] = accel[i] + accel_bias + rng.normal(0.0, accel_noise, size=3) + vib
        gyro_meas[i] = gyro[i] + gyro_bias + rng.normal(0.0, gyro_noise, size=3)

    q_mps2 = sensor_value(sensor, "quantization_mg") * G_MPS2 / 1000.0
    if q_mps2 > 0:
        accel_meas = np.round(accel_meas / q_mps2) * q_mps2

    window = int(mod.get("lowpass", 1))
    if window > 1:
        accel_meas = moving_average(accel_meas, window)
        gyro_meas = moving_average(gyro_meas, max(3, window // 2 * 2 + 1))

    jitter_s = sensor_value(sensor, "timestamp_jitter_ms") / 1000.0
    out["meas_time_s"] = out["imu_time_s"].to_numpy(float) + rng.normal(0.0, jitter_s, size=len(out))
    out["accel_x_mps2"] = accel_meas[:, 0]
    out["accel_y_mps2"] = accel_meas[:, 1]
    out["accel_z_mps2"] = accel_meas[:, 2]
    out["gyro_x_rad_s"] = gyro_meas[:, 0]
    out["gyro_y_rad_s"] = gyro_meas[:, 1]
    out["gyro_z_rad_s"] = gyro_meas[:, 2]
    out["accel_bias_sigma_mps2"] = accel_bias_sigma
    out["gyro_bias_sigma_rad_s"] = gyro_bias_sigma
    out["accel_noise_sigma_mps2"] = accel_noise
    out["gyro_noise_sigma_rad_s"] = gyro_noise
    out["vibration_amp_mps2"] = vib_amp
    return out


def yaw_to_forward(yaw: float) -> np.ndarray:
    return np.array([math.cos(yaw), 0.0, math.sin(yaw)], dtype=float)


def integrate_imu_packet(packet: pd.DataFrame, output_times: np.ndarray) -> np.ndarray:
    p = packet[["truth_x_mm", "truth_y_mm", "truth_z_mm"]].iloc[0].to_numpy(float) / 1000.0
    v = packet[["truth_vx_mps", "truth_vy_mps", "truth_vz_mps"]].iloc[0].to_numpy(float)
    yaw = float(packet["truth_yaw_rad"].iloc[0])
    times = packet["imu_time_s"].to_numpy(float)
    accel = packet[["accel_x_mps2", "accel_y_mps2", "accel_z_mps2"]].to_numpy(float)
    gyro_y = packet["gyro_y_rad_s"].to_numpy(float)
    pos_hist = np.empty((len(times), 3), dtype=float)
    pos_hist[0] = p
    for i in range(1, len(times)):
        dti = float(times[i] - times[i - 1])
        if not math.isfinite(dti) or dti <= 0:
            dti = 1.0 / 120.0
        rot_wb = rotation_from_forward(yaw_to_forward(yaw))
        acc_world = rot_wb @ accel[i - 1] + G_WORLD
        p = p + v * dti + 0.5 * acc_world * dti * dti
        v = v + acc_world * dti
        yaw += gyro_y[i - 1] * dti
        pos_hist[i] = p
    return interp_columns(times, pos_hist * 1000.0, output_times)


def fuse_uwb_imu_packet(packet: pd.DataFrame, uwb_track: pd.DataFrame, sensor: dict, i_id: str) -> tuple[np.ndarray, np.ndarray]:
    mod = I_MODS[i_id]
    times = packet["imu_time_s"].to_numpy(float)
    accel = packet[["accel_x_mps2", "accel_y_mps2", "accel_z_mps2"]].to_numpy(float)
    gyro_y = packet["gyro_y_rad_s"].to_numpy(float)
    meas_t = uwb_track["time_s"].to_numpy(float)
    meas_xyz_m = uwb_track[["uwb_x_mm", "uwb_y_mm", "uwb_z_mm"]].to_numpy(float) / 1000.0
    p = packet[["truth_x_mm", "truth_y_mm", "truth_z_mm"]].iloc[0].to_numpy(float) / 1000.0
    v = packet[["truth_vx_mps", "truth_vy_mps", "truth_vz_mps"]].iloc[0].to_numpy(float)
    yaw = float(packet["truth_yaw_rad"].iloc[0])
    state = np.concatenate([p, v])
    cov = np.diag([0.04, 0.04, 0.04, 0.25, 0.25, 0.25]).astype(float)
    accel_noise = max(
        sensor_value(sensor, "accel_noise_mg") * mod["noise"] * G_MPS2 / 1000.0,
        sensor_value(sensor, "residual_accel_bias_mg") * mod["bias"] * G_MPS2 / 1000.0,
        0.02,
    )
    measurement_sigma_m = 0.090
    meas_cov = np.diag([measurement_sigma_m**2, (1.35 * measurement_sigma_m) ** 2, measurement_sigma_m**2])
    h = np.zeros((3, 6), dtype=float)
    h[:, :3] = np.eye(3)
    fused_at_meas = np.full_like(meas_xyz_m, np.nan)
    nis_values = np.full(len(meas_t), np.nan, dtype=float)
    next_meas = 0
    for i in range(1, len(times)):
        dti = float(times[i] - times[i - 1])
        if not math.isfinite(dti) or dti <= 0:
            dti = 1.0 / 120.0
        rot_wb = rotation_from_forward(yaw_to_forward(yaw))
        acc_world = rot_wb @ accel[i - 1] + G_WORLD
        yaw += gyro_y[i - 1] * dti
        p = state[:3]
        v = state[3:]
        p = p + v * dti + 0.5 * acc_world * dti * dti
        v = v + acc_world * dti
        state = np.concatenate([p, v])

        f = np.eye(6)
        f[:3, 3:] = np.eye(3) * dti
        q = accel_noise**2
        q_block = np.block(
            [
                [np.eye(3) * (0.25 * dti**4 * q), np.eye(3) * (0.5 * dti**3 * q)],
                [np.eye(3) * (0.5 * dti**3 * q), np.eye(3) * (dti**2 * q)],
            ]
        )
        cov = f @ cov @ f.T + q_block

        while next_meas < len(meas_t) and meas_t[next_meas] <= times[i] + 0.5 * dti:
            z = meas_xyz_m[next_meas]
            if np.isfinite(z).all():
                innov = z - h @ state
                s_mat = h @ cov @ h.T + meas_cov
                try:
                    s_inv = np.linalg.inv(s_mat)
                    nis = float(innov.T @ s_inv @ innov)
                    gate = nis < 25.0
                    if gate:
                        k = cov @ h.T @ s_inv
                        state = state + k @ innov
                        cov = (np.eye(6) - k @ h) @ cov
                    nis_values[next_meas] = nis
                except np.linalg.LinAlgError:
                    pass
            fused_at_meas[next_meas] = state[:3] * 1000.0
            next_meas += 1
    if next_meas < len(meas_t):
        tail = interp_columns(times, np.tile(state[:3] * 1000.0, (len(times), 1)), meas_t[next_meas:])
        fused_at_meas[next_meas:] = tail
    return fused_at_meas, nis_values


def forward_imu_corrects_uwb(uwb_xyz_mm: np.ndarray, imu_xyz_mm: np.ndarray) -> np.ndarray:
    """Causal IMU-motion-prior correction of solved UWB positions."""
    z = pd.DataFrame(uwb_xyz_mm).interpolate(limit_direction="both").to_numpy(float)
    imu = pd.DataFrame(imu_xyz_mm).interpolate(limit_direction="both").to_numpy(float)
    out = np.empty_like(z)
    out[0] = z[0]
    for i in range(1, len(z)):
        delta = imu[i] - imu[i - 1]
        pred = out[i - 1] + delta
        innov = z[i] - pred
        innov_norm = float(np.linalg.norm(innov))
        # Small innovations trust UWB; large jumps are pulled toward the IMU
        # motion prior. This corrects solved-UWB spikes without updating an IMU
        # bias state, so the coupling mode is IMU -> UWB.
        alpha = 0.42 if innov_norm < 180.0 else 0.18
        out[i] = pred + alpha * innov
    return out


def forward_bidirectional_fusion(
    packet: pd.DataFrame,
    uwb_track: pd.DataFrame,
    sensor: dict,
    i_id: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Causal bidirectional-lite fusion.

    This deliberately keeps the vertical-slice row stable: one branch is
    UWB->IMU (`T5LITE`), the other is IMU->UWB (`T2LITE`), and the output is a
    same-time causal blend. It is a coupling-direction smoke, not a final ESKF.
    """
    meas_t = uwb_track["time_s"].to_numpy(float)
    uwb_xyz = uwb_track[["uwb_x_mm", "uwb_y_mm", "uwb_z_mm"]].to_numpy(float)
    imu_xyz = integrate_imu_packet(packet, meas_t)
    imu2uwb = forward_imu_corrects_uwb(uwb_xyz, imu_xyz)
    uwb2imu, nis_values = fuse_uwb_imu_packet(packet, uwb_track, sensor, i_id)
    out = 0.68 * uwb2imu + 0.32 * imu2uwb
    bad = ~np.isfinite(out).all(axis=1)
    if np.any(bad):
        out[bad] = uwb2imu[bad]
    return out, nis_values


def resolve_torch_device(device_arg: str) -> tuple[str, dict]:
    try:
        import torch
    except Exception as exc:  # pragma: no cover - environment dependent
        return "cpu", {"torch_available": False, "error": str(exc), "requested_device": device_arg}

    info: dict[str, object] = {
        "torch_available": True,
        "torch_version": getattr(torch, "__version__", ""),
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
        "requested_device": device_arg,
    }
    if torch.cuda.is_available():
        info["cuda_devices"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
    device = "cuda:0" if device_arg == "auto" and torch.cuda.is_available() else ("cpu" if device_arg == "auto" else device_arg)
    if device.startswith("cuda") and not torch.cuda.is_available():
        device = "cpu"
        info["fallback_reason"] = "cuda_requested_but_unavailable"
    info["resolved_device"] = device
    return device, info


def torch_huber(x, delta: float):
    import torch

    ax = torch.abs(x)
    return torch.where(ax <= delta, 0.5 * x * x, delta * (ax - 0.5 * delta))


def session_smooth_positions_torch(
    uwb_xyz_mm: np.ndarray,
    imu_xyz_mm: np.ndarray,
    times_s: np.ndarray,
    mode: str,
    device: str,
    iters: int,
    lr: float,
    anchor_xyz_mm: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Full-session GPU-capable position-domain smoother."""
    import torch

    if mode not in {"uwb_corrects_imu", "imu_corrects_uwb", "bidirectional_joint"}:
        raise ValueError(f"unsupported session mode={mode}")
    if len(uwb_xyz_mm) < 3:
        return uwb_xyz_mm.copy(), {"session_loss": float("nan")}
    dev = torch.device(device)
    dtype = torch.float32
    z_np = np.asarray(uwb_xyz_mm, dtype=np.float32)
    imu_np = np.asarray(imu_xyz_mm, dtype=np.float32)
    anchor_np = np.asarray(anchor_xyz_mm, dtype=np.float32) if anchor_xyz_mm is not None else z_np
    t_np = np.asarray(times_s, dtype=np.float32)
    good = np.isfinite(z_np).all(axis=1) & np.isfinite(imu_np).all(axis=1) & np.isfinite(anchor_np).all(axis=1) & np.isfinite(t_np)
    if int(np.sum(good)) < 3:
        return z_np.astype(float), {"session_loss": float("nan")}
    z_fill = pd.DataFrame(z_np).interpolate(limit_direction="both").to_numpy(np.float32).copy()
    imu_fill = pd.DataFrame(imu_np).interpolate(limit_direction="both").to_numpy(np.float32).copy()
    anchor_fill = pd.DataFrame(anchor_np).interpolate(limit_direction="both").to_numpy(np.float32).copy()
    dt_np = np.diff(t_np)
    dt_good = dt_np[np.isfinite(dt_np) & (dt_np > 0)]
    dt_np[~np.isfinite(dt_np) | (dt_np <= 0)] = float(np.nanmedian(dt_good)) if len(dt_good) else 1.0 / 30.0

    z = torch.as_tensor(z_fill, dtype=dtype, device=dev)
    anchor = torch.as_tensor(anchor_fill, dtype=dtype, device=dev)
    mask = torch.as_tensor(good.astype(np.float32), dtype=dtype, device=dev)[:, None]
    imu = torch.as_tensor(imu_fill, dtype=dtype, device=dev)
    imu_delta = imu[1:] - imu[:-1]
    dt = torch.as_tensor(dt_np, dtype=dtype, device=dev)[:, None]

    x = torch.nn.Parameter(anchor.clone())
    params: list[torch.nn.Parameter] = [x]
    drift_rate = None
    if mode == "bidirectional_joint":
        total_dt = float(np.nansum(dt_np))
        init_drift = (imu_fill[-1] - imu_fill[0] - (z_fill[-1] - z_fill[0])) / max(total_dt, 1.0e-6)
        drift_rate = torch.nn.Parameter(torch.as_tensor(init_drift.reshape(1, 3), dtype=dtype, device=dev))
        params.append(drift_rate)

    abs_sigma = 155.0 if mode == "uwb_corrects_imu" else (150.0 if mode == "imu_corrects_uwb" else 165.0)
    anchor_sigma = 65.0 if mode in {"uwb_corrects_imu", "bidirectional_joint"} else 80.0
    rel_sigma = 90.0 if mode == "uwb_corrects_imu" else (55.0 if mode == "imu_corrects_uwb" else 50.0)
    smooth_sigma = 240.0
    drift_sigma = 4500.0
    robust_delta = 2.8
    rel_weight = 0.30 if mode == "uwb_corrects_imu" else (0.48 if mode == "imu_corrects_uwb" else 0.55)
    anchor_weight = 0.85 if mode == "uwb_corrects_imu" else (0.65 if mode == "imu_corrects_uwb" else 0.72)
    smooth_weight = 0.045
    opt = torch.optim.Adam(params, lr=lr)
    last_loss = float("nan")
    for _ in range(max(1, int(iters))):
        opt.zero_grad(set_to_none=True)
        abs_res = (x - z) / abs_sigma
        abs_loss = (torch_huber(abs_res, robust_delta) * mask).sum() / torch.clamp(mask.sum() * 3.0, min=1.0)
        anchor_res = (x - anchor) / anchor_sigma
        anchor_loss = (torch_huber(anchor_res, 2.5) * mask).sum() / torch.clamp(mask.sum() * 3.0, min=1.0)
        corrected_delta = imu_delta
        drift_loss = torch.zeros((), dtype=dtype, device=dev)
        if drift_rate is not None:
            corrected_delta = imu_delta - drift_rate * dt
            drift_loss = torch.mean((drift_rate / drift_sigma) ** 2)
        rel_res = (x[1:] - x[:-1] - corrected_delta) / rel_sigma
        rel_mask = mask[1:] * mask[:-1]
        rel_loss = (rel_res * rel_res * rel_mask).sum() / torch.clamp(rel_mask.sum() * 3.0, min=1.0)
        second = (x[2:] - 2.0 * x[1:-1] + x[:-2]) / smooth_sigma
        second_mask = mask[2:] * mask[1:-1] * mask[:-2]
        smooth_loss = (second * second * second_mask).sum() / torch.clamp(second_mask.sum() * 3.0, min=1.0)
        loss = abs_loss + anchor_weight * anchor_loss + rel_weight * rel_loss + smooth_weight * smooth_loss + 0.02 * drift_loss
        loss.backward()
        opt.step()
        last_loss = float(loss.detach().cpu())
    if dev.type == "cuda":
        torch.cuda.synchronize(dev)
    out = x.detach().cpu().numpy().astype(float)
    stats = {
        "session_loss": last_loss,
        "session_iters": float(iters),
        "session_lr": float(lr),
        "session_abs_sigma_mm": float(abs_sigma),
        "session_anchor_sigma_mm": float(anchor_sigma),
        "session_rel_sigma_mm": float(rel_sigma),
        "session_anchor_weight": float(anchor_weight),
        "session_rel_weight": float(rel_weight),
        "session_smooth_weight": float(smooth_weight),
    }
    if drift_rate is not None:
        d = drift_rate.detach().cpu().numpy().reshape(3)
        stats.update(
            {
                "session_drift_rate_x_mm_s": float(d[0]),
                "session_drift_rate_y_mm_s": float(d[1]),
                "session_drift_rate_z_mm_s": float(d[2]),
                "session_drift_rate_3d_mm_s": float(np.linalg.norm(d)),
            }
        )
    return out, stats


def apply_row_metadata(
    df: pd.DataFrame,
    *,
    information_use: str,
    coupling_mode: str,
    solver_family: str,
    solver_detail: str,
    gpu_backend: str = "",
) -> pd.DataFrame:
    df["information_use"] = information_use
    df["coupling_mode"] = coupling_mode
    df["solver_family"] = solver_family
    df["solver_detail"] = solver_detail
    df["gpu_backend"] = gpu_backend
    return df


def resolve_torch_devices(device_args: list[str]) -> tuple[list[str], dict]:
    try:
        import torch
    except Exception as exc:  # pragma: no cover - environment dependent
        return ["cpu"], {"torch_available": False, "error": str(exc), "requested_devices": device_args}

    info: dict[str, object] = {
        "torch_available": True,
        "torch_version": getattr(torch, "__version__", ""),
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
        "requested_devices": device_args,
    }
    if torch.cuda.is_available():
        info["cuda_devices"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
    if device_args == ["auto"]:
        devices = [f"cuda:{i}" for i in range(torch.cuda.device_count())] if torch.cuda.is_available() else ["cpu"]
    else:
        devices = list(device_args)
    if any(d.startswith("cuda") for d in devices) and not torch.cuda.is_available():
        devices = ["cpu"]
        info["fallback_reason"] = "cuda_requested_but_unavailable"
    info["resolved_devices"] = devices
    return devices, info


def session_exp_id(l_id: str, i_id: str, mode: str) -> str:
    suffix = {
        "uwb_corrects_imu": "T9LITE_UWB2IMU",
        "imu_corrects_uwb": "T9LITE_IMU2UWB",
        "bidirectional_joint": "T10LITE_BIDIR",
    }[mode]
    return f"REAL6_X_A0_U4_P0_{l_id}_{i_id}_{suffix}"


def session_description(l_id: str, i_id: str, mode: str) -> str:
    desc = {
        "uwb_corrects_imu": "full-session UWB-corrects-IMU smoother using solved UWB positions plus IMU relative motion.",
        "imu_corrects_uwb": "full-session IMU-corrects-UWB smoother using IMU relative motion to regularize solved UWB positions.",
        "bidirectional_joint": "full-session bidirectional joint smoother with UWB positions, IMU relative motion, and IMU drift-rate proxy.",
    }[mode]
    return f"Realistic simulated 6-axis {l_id}/{i_id} {desc}"


def run_session_task_batch(tasks: list[dict]) -> list[dict]:
    results: list[dict] = []
    for task in tasks:
        xyz, stats = session_smooth_positions_torch(
            task["uwb_xyz_mm"],
            task["imu_xyz_mm"],
            task["times_s"],
            task["mode"],
            task["device"],
            task["iters"],
            task["lr"],
            task.get("anchor_xyz_mm"),
        )
        exp = session_exp_id(task["l_id"], task["i_id"], task["mode"])
        row = make_row_from_positions(task["track"], exp, xyz, session_description(task["l_id"], task["i_id"], task["mode"]))
        row = apply_row_metadata(
            row,
            information_use="full-session",
            coupling_mode=task["mode"],
            solver_family="T10LITE" if task["mode"] == "bidirectional_joint" else "T9LITE",
            solver_detail="torch full-session position-domain objective anchored by matching forward fusion row",
            gpu_backend=task["device"],
        )
        for key, val in stats.items():
            row[key] = val
        results.append({"experiment_id": exp, "row": row, "stats": stats, "device": task["device"]})
    return results


def make_row_from_positions(base_track: pd.DataFrame, experiment_id: str, xyz_mm: np.ndarray, description: str) -> pd.DataFrame:
    out = base_track.copy()
    out["experiment_id"] = experiment_id
    out["deployability"] = "real_6axis_vertical_slice"
    out["description"] = description
    out["x_mm"] = xyz_mm[:, 0]
    out["y_mm"] = xyz_mm[:, 1]
    out["z_mm"] = xyz_mm[:, 2]
    P1.add_errors(out)
    return out


def summarize_rows(samples_by_exp: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict] = []
    track_rows: list[dict] = []
    for exp, df in samples_by_exp.items():
        deploy = str(df["deployability"].iloc[0])
        desc = str(df["description"].iloc[0])
        tracks, summary = P1.track_metrics(df, exp, deploy, desc)
        track_rows.extend(tracks)
        summary_rows.append(summary)
    return pd.DataFrame(summary_rows), pd.DataFrame(track_rows)


def split_accuracy_summary(samples_by_exp: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict] = []
    for exp, df in samples_by_exp.items():
        track_rows: list[dict] = []
        for (capture_id, tag), g in df.groupby(["capture_id", "tag"], sort=True):
            err_x = g["err_x_mm"].to_numpy(float)
            err_y = g["err_y_mm"].to_numpy(float)
            err_z = g["err_z_mm"].to_numpy(float)
            track_rows.append(
                {
                    "experiment_id": exp,
                    "capture_id": capture_id,
                    "tag": tag,
                    "err3d_p50_mm": P1.pct(g["err3d_mm"].to_numpy(float), 50),
                    "err3d_p95_mm": P1.pct(g["err3d_mm"].to_numpy(float), 95),
                    # Project convention: official samples use y_vertical, so
                    # the horizontal plane is X-Z and the vertical component is Y.
                    "project_horizontal_xz_p50_mm": P1.pct(g["err_horizontal_xz_mm"].to_numpy(float), 50),
                    "project_horizontal_xz_p95_mm": P1.pct(g["err_horizontal_xz_mm"].to_numpy(float), 95),
                    "project_vertical_y_p50_mm": P1.pct(g["err_vertical_y_mm"].to_numpy(float), 50),
                    "project_vertical_y_p95_mm": P1.pct(g["err_vertical_y_mm"].to_numpy(float), 95),
                    # File-native split requested as XY/Z. This is useful for
                    # direct column math, but remember Y is vertical here.
                    "file_xy_p50_mm": P1.pct(np.hypot(err_x, err_y), 50),
                    "file_xy_p95_mm": P1.pct(np.hypot(err_x, err_y), 95),
                    "file_z_p50_mm": P1.pct(np.abs(err_z), 50),
                    "file_z_p95_mm": P1.pct(np.abs(err_z), 95),
                }
            )
        tracks = pd.DataFrame(track_rows)
        out = {
            "experiment_id": exp,
            "information_use": str(df["information_use"].iloc[0]) if "information_use" in df.columns else "",
            "coupling_mode": str(df["coupling_mode"].iloc[0]) if "coupling_mode" in df.columns else "",
            "solver_family": str(df["solver_family"].iloc[0]) if "solver_family" in df.columns else "",
        }
        for col in [
            "err3d_p50_mm",
            "err3d_p95_mm",
            "project_horizontal_xz_p50_mm",
            "project_horizontal_xz_p95_mm",
            "project_vertical_y_p50_mm",
            "project_vertical_y_p95_mm",
            "file_xy_p50_mm",
            "file_xy_p95_mm",
            "file_z_p50_mm",
            "file_z_p95_mm",
        ]:
            out[f"trackmedian_{col}"] = float(np.nanmedian(tracks[col].to_numpy(float))) if not tracks.empty else float("nan")
        rows.append(out)
    return pd.DataFrame(rows)


def exp_label(exp: str) -> str:
    if exp == "REAL6_B0_A0_U4_P0_T1":
        return "pure UWB"
    if exp == "REAL6_X_A0_L0_I0_T11":
        return "perfect IMU"
    if "T2LITE_IMU2UWB" in exp:
        return "Fwd IMU->UWB"
    if "T3LITE_BIDIR" in exp:
        return "Fwd Bidir"
    if "T5LITE" in exp:
        return "Fwd UWB->IMU"
    if "T9LITE_UWB2IMU" in exp:
        return "Sess UWB->IMU"
    if "T9LITE_IMU2UWB" in exp:
        return "Sess IMU->UWB"
    if "T10LITE_BIDIR" in exp:
        return "Sess Bidir"
    if exp.endswith("_T11"):
        return "realistic IMU"
    return exp


def exp_color(exp: str) -> str:
    if exp == "REAL6_B0_A0_U4_P0_T1":
        return "#2f7ed8"
    if exp == "REAL6_X_A0_L0_I0_T11":
        return "#36a657"
    if "T2LITE_IMU2UWB" in exp:
        return "#e0a82e"
    if "T3LITE_BIDIR" in exp:
        return "#cc5aa8"
    if "T5LITE" in exp:
        return "#8b55c7"
    if "T9LITE_UWB2IMU" in exp:
        return "#6042a6"
    if "T9LITE_IMU2UWB" in exp:
        return "#cc7a00"
    if "T10LITE_BIDIR" in exp:
        return "#0b8f8f"
    if exp.endswith("_T11"):
        return "#d64b3c"
    return "#555555"


def ordered_experiments(samples_by_exp: dict[str, pd.DataFrame]) -> list[str]:
    priority = {
        "REAL6_B0_A0_U4_P0_T1": 0,
        "REAL6_X_A0_L0_I0_T11": 1,
    }

    def key(exp: str) -> tuple[int, str]:
        if exp in priority:
            return (priority[exp], exp)
        if exp.endswith("_T11"):
            return (2, exp)
        if "T2LITE_IMU2UWB" in exp:
            return (3, exp)
        if "T3LITE_BIDIR" in exp:
            return (4, exp)
        if "T5LITE" in exp:
            return (5, exp)
        if "T9LITE_UWB2IMU" in exp:
            return (6, exp)
        if "T9LITE_IMU2UWB" in exp:
            return (7, exp)
        if "T10LITE_BIDIR" in exp:
            return (8, exp)
        return (10, exp)

    return sorted(samples_by_exp, key=key)


def safe_filename(text: str) -> str:
    keep = []
    for ch in text:
        keep.append(ch if ch.isalnum() or ch in "._-" else "_")
    return "".join(keep).strip("_")


def plot_overlay_sheet(samples_by_exp: dict[str, pd.DataFrame], out_path: Path, selected_captures: Iterable[str] | None = None) -> None:
    exp_order = ordered_experiments(samples_by_exp)
    base = samples_by_exp[exp_order[0]]
    keys = sorted(base.groupby(["capture_id", "tag"]).groups)
    if selected_captures is not None:
        sel = set(selected_captures)
        keys = [k for k in keys if k[0] in sel]
    n = len(keys)
    cols = 4 if n > 12 else 2
    rows = int(math.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 4.2, rows * 3.8), squeeze=False)
    by_exp_track = {
        exp: {k: g.sort_values("time_s") for k, g in df.groupby(["capture_id", "tag"], sort=True)}
        for exp, df in samples_by_exp.items()
    }
    for ax, key in zip(axes.flat, keys):
        opti = by_exp_track[exp_order[0]][key]
        ax.plot(opti["opti_x_mm"], opti["opti_z_mm"], color="black", lw=1.5, label="Opti")
        for exp in exp_order:
            g = by_exp_track[exp][key]
            lw = 1.2 if "T5LITE" not in exp else 1.8
            alpha = 0.85 if "T11" not in exp else 0.75
            ax.plot(g["x_mm"], g["z_mm"], color=exp_color(exp), lw=lw, alpha=alpha, label=exp_label(exp))
        ax.set_title(f"{key[0]}/{key[1]}", fontsize=8)
        ax.set_aspect("equal", adjustable="box")
        ax.tick_params(labelsize=6)
        ax.grid(True, alpha=0.18)
    for ax in axes.flat[n:]:
        ax.axis("off")
    handles, lab = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, lab, loc="upper center", ncol=5, fontsize=9)
    fig.suptitle("Real 6-axis simulated IMU vertical slice: Opti vs UWB vs IMU vs UWB+IMU", y=0.997, fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.985])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def plot_single_method_sheet(
    samples_by_exp: dict[str, pd.DataFrame],
    exp: str,
    out_path: Path,
    selected_captures: Iterable[str] | None = None,
) -> None:
    base = samples_by_exp[ordered_experiments(samples_by_exp)[0]]
    method = samples_by_exp[exp]
    keys = sorted(base.groupby(["capture_id", "tag"]).groups)
    if selected_captures is not None:
        sel = set(selected_captures)
        keys = [k for k in keys if k[0] in sel]
    n = len(keys)
    cols = 4 if n > 12 else 2
    rows = int(math.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 4.2, rows * 3.8), squeeze=False)
    base_by_track = {k: g.sort_values("time_s") for k, g in base.groupby(["capture_id", "tag"], sort=True)}
    method_by_track = {k: g.sort_values("time_s") for k, g in method.groupby(["capture_id", "tag"], sort=True)}
    label = exp_label(exp)
    color = exp_color(exp)
    for ax, key in zip(axes.flat, keys):
        opti = base_by_track[key]
        g = method_by_track[key]
        ax.plot(opti["opti_x_mm"], opti["opti_z_mm"], color="black", lw=1.6, label="Opti")
        ax.plot(g["x_mm"], g["z_mm"], color=color, lw=1.8, alpha=0.92, label=label)
        p50 = P1.pct(g["err3d_mm"].to_numpy(float), 50)
        p95 = P1.pct(g["err3d_mm"].to_numpy(float), 95)
        ax.set_title(f"{key[0]}/{key[1]}  P50={p50:.0f} P95={p95:.0f} mm", fontsize=8)
        ax.set_aspect("equal", adjustable="box")
        ax.tick_params(labelsize=6)
        ax.grid(True, alpha=0.18)
    for ax in axes.flat[n:]:
        ax.axis("off")
    axes.flat[0].legend(loc="upper right", fontsize=7, frameon=True)
    fig.suptitle(f"Real 6-axis simulated IMU vertical slice: {label} vs Opti", y=0.997, fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.985])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def plot_method_sheets(
    samples_by_exp: dict[str, pd.DataFrame],
    run_dir: Path,
    selected_captures: Iterable[str] | None = None,
) -> dict[str, list[str]]:
    outputs: dict[str, list[str]] = {"full": [], "selected": []}
    for idx, exp in enumerate(ordered_experiments(samples_by_exp), start=1):
        stem = f"{idx:02d}_{safe_filename(exp_label(exp))}__{safe_filename(exp)}.png"
        full_path = run_dir / "figs" / "method_sheets" / "full" / stem
        plot_single_method_sheet(samples_by_exp, exp, full_path)
        outputs["full"].append(str(full_path.relative_to(run_dir)))
        if selected_captures is not None:
            selected_path = run_dir / "figs" / "method_sheets" / "selected" / stem
            plot_single_method_sheet(samples_by_exp, exp, selected_path, selected_captures=selected_captures)
        outputs["selected"].append(str(selected_path.relative_to(run_dir)))
    return outputs


def plot_opti_uwb_fused_sheet(
    samples_by_exp: dict[str, pd.DataFrame],
    out_path: Path,
    selected_captures: Iterable[str] | None = None,
) -> None:
    uwb_exp = "REAL6_B0_A0_U4_P0_T1"
    fused_exps = [exp for exp in samples_by_exp if "T5LITE" in exp]
    if uwb_exp not in samples_by_exp or not fused_exps:
        return
    fused_exp = fused_exps[0]
    base = samples_by_exp[uwb_exp]
    fused = samples_by_exp[fused_exp]
    keys = sorted(base.groupby(["capture_id", "tag"]).groups)
    if selected_captures is not None:
        sel = set(selected_captures)
        keys = [k for k in keys if k[0] in sel]
    n = len(keys)
    cols = 4 if n > 12 else 2
    rows = int(math.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 4.2, rows * 3.8), squeeze=False)
    uwb_by_track = {k: g.sort_values("time_s") for k, g in base.groupby(["capture_id", "tag"], sort=True)}
    fused_by_track = {k: g.sort_values("time_s") for k, g in fused.groupby(["capture_id", "tag"], sort=True)}
    for ax, key in zip(axes.flat, keys):
        uwb = uwb_by_track[key]
        fused_g = fused_by_track[key]
        ax.plot(uwb["opti_x_mm"], uwb["opti_z_mm"], color="black", lw=1.7, label="Opti")
        ax.plot(uwb["x_mm"], uwb["z_mm"], color="#2f7ed8", lw=1.25, alpha=0.78, label="pure UWB")
        ax.plot(fused_g["x_mm"], fused_g["z_mm"], color="#8b55c7", lw=1.8, alpha=0.90, label="UWB+IMU")
        uwb_p50 = P1.pct(uwb["err3d_mm"].to_numpy(float), 50)
        fused_p50 = P1.pct(fused_g["err3d_mm"].to_numpy(float), 50)
        ax.set_title(f"{key[0]}/{key[1]}  UWB={uwb_p50:.0f}  Fused={fused_p50:.0f} mm", fontsize=8)
        ax.set_aspect("equal", adjustable="box")
        ax.tick_params(labelsize=6)
        ax.grid(True, alpha=0.18)
    for ax in axes.flat[n:]:
        ax.axis("off")
    axes.flat[0].legend(loc="upper right", fontsize=7, frameon=True)
    fig.suptitle("Opti vs pure UWB vs UWB+IMU", y=0.997, fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.985])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def plot_triad_sheets(
    samples_by_exp: dict[str, pd.DataFrame],
    run_dir: Path,
    selected_captures: Iterable[str] | None = None,
) -> dict[str, str]:
    full_path = run_dir / "figs" / "triad_sheets" / "opti_vs_pure_uwb_vs_uwb_imu_full.png"
    selected_path = run_dir / "figs" / "triad_sheets" / "opti_vs_pure_uwb_vs_uwb_imu_selected.png"
    plot_opti_uwb_fused_sheet(samples_by_exp, full_path)
    plot_opti_uwb_fused_sheet(samples_by_exp, selected_path, selected_captures=selected_captures)
    return {
        "full": str(full_path.relative_to(run_dir)),
        "selected": str(selected_path.relative_to(run_dir)),
    }


def markdown_table(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return ""
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        vals = []
        for col in cols:
            val = row.get(col, "")
            if isinstance(val, float):
                vals.append(f"{val:.1f}" if math.isfinite(val) else "nan")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(
    run_dir: Path,
    run_id: str,
    summary: pd.DataFrame,
    split_summary: pd.DataFrame,
    sensor: dict,
    args: argparse.Namespace,
    method_pngs: dict[str, list[str]],
    triad_pngs: dict[str, str],
) -> None:
    cols = [
        "experiment_id",
        "information_use",
        "coupling_mode",
        "solver_family",
        "gpu_backend",
        "trackmedian_err3d_p50_mm",
        "trackmedian_err3d_p95_mm",
        "legacy_deltaR_error_rms_mm",
        "imu_only_endpoint_drift_3d_trackmedian_mm",
        "uwb_update_accept_rate_trackmedian",
    ]
    split_cols = [
        "experiment_id",
        "information_use",
        "coupling_mode",
        "trackmedian_err3d_p50_mm",
        "trackmedian_err3d_p95_mm",
        "trackmedian_project_horizontal_xz_p50_mm",
        "trackmedian_project_horizontal_xz_p95_mm",
        "trackmedian_project_vertical_y_p50_mm",
        "trackmedian_project_vertical_y_p95_mm",
        "trackmedian_file_xy_p50_mm",
        "trackmedian_file_xy_p95_mm",
        "trackmedian_file_z_p50_mm",
        "trackmedian_file_z_p95_mm",
        "legacy_deltaR_error_rms_mm",
    ]
    lines = [
        "# Real 6-Axis IMU Vertical Slice",
        "",
        f"Generated: {dt.datetime.now(dt.UTC).isoformat()}",
        f"Run ID: `{run_id}`",
        "",
        "## Purpose",
        "",
        "This run is the first explicit simulated 6-axis IMU packet smoke test.",
        "It is not the older position-domain drift-prior proxy.",
        "",
        "## Chosen Solver Matrix",
        "",
        "```text",
        "A0 + U4/P0 + L15 + I1+I3",
        "",
        "forward solver:",
        "  T5LITE_UWB2IMU  = UWB corrects IMU",
        "  T2LITE_IMU2UWB  = IMU corrects UWB",
        "  T3LITE_BIDIR    = causal bidirectional-lite",
        "",
        "session solver:",
        "  T9LITE_UWB2IMU  = full-session UWB corrects IMU",
        "  T9LITE_IMU2UWB  = full-session IMU corrects UWB",
        "  T10LITE_BIDIR   = full-session bidirectional-lite",
        "```",
        "",
        "Rationale:",
        "",
        "- `L15` = InvenSense ICM-42688-P high-precision consumer/drone 6-axis IMU.",
        "- `I1+I3` = low-pass + residual bias/random-walk model.",
        "- Forward rows are causal-position-domain vertical slices.",
        "- Session rows are torch full-session position-domain objectives anchored by the matching forward fusion row; they are GPU-capable and should be run with `cuda:0 cuda:1` when available.",
        "",
        "## IMU Packet Model",
        "",
        "```text",
        "Opti/Vicon xyz trajectory",
        f"-> orientation mode: {args.orientation_mode}",
        "-> accel_x/y/z + gyro_x/y/z at ODR",
        "-> gravity/world-frame transform",
        "-> L15 noise/bias/random-walk/vibration/timestamp/quantization",
        "-> I1+I3 low-pass and residual reduction",
        "-> pure IMU dead reckoning and UWB+IMU fusion",
        "```",
        "",
        "Important limitation:",
        "",
        "```text",
        f"orientation_source = {args.orientation_mode}",
        "The official B0 sample table has Opti xyz only. This smoke does not yet",
        "use a full exported Vicon rigid-body quaternion. The default smoke uses",
        "a world-aligned body frame so the perfect-IMU control is numerically valid.",
        "```",
        "",
        "Sensor fields used:",
        "",
        "```text",
        f"name = {sensor.get('name')}",
        f"accel_noise_mg = {sensor.get('accel_noise_mg')}",
        f"gyro_noise_dps = {sensor.get('gyro_noise_dps')}",
        f"residual_accel_bias_mg = {sensor.get('residual_accel_bias_mg')}",
        f"residual_gyro_bias_dps = {sensor.get('residual_gyro_bias_dps')}",
        f"accel_bias_random_walk_mg_sqrt_s = {sensor.get('accel_bias_random_walk_mg_sqrt_s')}",
        f"gyro_bias_random_walk_dps_sqrt_s = {sensor.get('gyro_bias_random_walk_dps_sqrt_s')}",
        f"timestamp_jitter_ms = {sensor.get('timestamp_jitter_ms')}",
        f"quantization_mg = {sensor.get('quantization_mg')}",
        f"vibration_sensitivity_mg = {sensor.get('vibration_sensitivity_mg')}",
        f"extrinsic_mg = {sensor.get('extrinsic_mg')}",
        "```",
        "",
        "## Summary",
        "",
        markdown_table(summary, [c for c in cols if c in summary.columns]),
        "",
        "## Split Accuracy",
        "",
        "Coordinate note:",
        "",
        "```text",
        "Project convention: Y is vertical, so horizontal plane = X-Z and vertical axis = Y.",
        "File-native XY/Z columns are also reported for direct column-level checks.",
        "```",
        "",
        markdown_table(split_summary, [c for c in split_cols if c in split_summary.columns]),
        "",
        "## Outputs",
        "",
        "```text",
        "tables/real_6axis_summary.csv",
        "tables/real_6axis_split_accuracy.csv",
        "tables/real_6axis_track_metrics.csv",
        "traces/real_6axis_samples.csv.gz",
        "traces/real_6axis_imu_packets.csv.gz",
        "figs/contact_sheets/real_6axis_overlay_full.png",
        "figs/contact_sheets/real_6axis_overlay_selected.png",
        "figs/method_sheets/full/*.png",
        "figs/method_sheets/selected/*.png",
        "figs/triad_sheets/opti_vs_pure_uwb_vs_uwb_imu_full.png",
        "figs/triad_sheets/opti_vs_pure_uwb_vs_uwb_imu_selected.png",
        "```",
        "",
        "One-method-per-figure PNGs:",
        "",
        "```text",
        *method_pngs.get("full", []),
        *method_pngs.get("selected", []),
        "```",
        "",
        "Opti + pure UWB + UWB+IMU comparison PNGs:",
        "",
        "```text",
        triad_pngs.get("full", ""),
        triad_pngs.get("selected", ""),
        "```",
        "",
        "## Phase 4 Consequence",
        "",
        "This smoke proves the simulation line can produce and consume explicit",
        "`ax/ay/az/gx/gy/gz` packets. The official Phase 4 FULL runner still needs",
        "the same packet interface wired into real T5/T6/T7/T8/T9/T10 implementations",
        "and should not rely on the old drift-prior proxy.",
    ]
    (run_dir / "reports" / "REAL_6AXIS_VERTICAL_SLICE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a real simulated 6-axis IMU + UWB vertical slice.")
    parser.add_argument("--run-id", default=utc_run_id())
    parser.add_argument("--l-id", default="L15")
    parser.add_argument("--i-id", default="I1+I3")
    parser.add_argument("--odr-hz", type=float, default=120.0)
    parser.add_argument("--orientation-mode", choices=["world_aligned", "trajectory_yaw"], default="world_aligned")
    parser.add_argument("--selected-captures", nargs="*", default=["R01", "R05", "R09", "R13", "R17"])
    parser.add_argument("--session-devices", nargs="+", default=["auto"], help="Torch devices for session solvers, e.g. auto or cuda:0 cuda:1.")
    parser.add_argument("--session-iters", type=int, default=260, help="Torch optimizer iterations for each session row/track.")
    parser.add_argument("--session-lr", type=float, default=0.075, help="Torch optimizer learning rate for session rows.")
    args = parser.parse_args()

    if args.i_id not in I_MODS:
        raise SystemExit(f"unsupported I id for smoke: {args.i_id}")

    sensors = load_sensors()
    if args.l_id not in sensors:
        raise SystemExit(f"sensor {args.l_id} not found in configs/sensors.yaml")
    sensor = sensors[args.l_id]
    session_devices, session_device_info = resolve_torch_devices(args.session_devices)
    run_dir = SIM_ROOT / "runs" / "phase4_real_6axis_vertical_slice" / args.run_id
    ensure_dirs(run_dir)

    b0_case = P1.BASELINES[0]
    b0_raw = P1.load_official_samples(b0_case.sample_path)
    base = P1.official_to_samples(b0_raw, b0_case.experiment_id, b0_case.deployability, b0_case.description)
    base = base.sort_values(["capture_id", "tag", "time_s"]).copy()

    rows_uwb: list[pd.DataFrame] = []
    rows_perfect_imu: list[pd.DataFrame] = []
    rows_real_imu: list[pd.DataFrame] = []
    rows_fused: list[pd.DataFrame] = []
    rows_forward_imu2uwb: list[pd.DataFrame] = []
    rows_forward_bidir: list[pd.DataFrame] = []
    packet_chunks: list[pd.DataFrame] = []
    session_tasks: list[dict] = []

    for (capture_id, tag), track in base.groupby(["capture_id", "tag"], sort=True):
        if str(tag) not in TAGS:
            continue
        g = track.sort_values("time_s").copy()
        nominal = build_nominal_packet(g, args.odr_hz, args.orientation_mode)
        perfect_packet = nominal.copy()
        perfect_packet["meas_time_s"] = perfect_packet["imu_time_s"]
        real_packet = apply_sensor_model(nominal, sensor, args.i_id, args.run_id, str(capture_id), str(tag))

        output_times = g["time_s"].to_numpy(float)
        perfect_xyz = integrate_imu_packet(perfect_packet, output_times)
        real_xyz = integrate_imu_packet(real_packet, output_times)
        fused_xyz, nis = fuse_uwb_imu_packet(real_packet, g, sensor, args.i_id)
        forward_imu2uwb_xyz = forward_imu_corrects_uwb(g[["uwb_x_mm", "uwb_y_mm", "uwb_z_mm"]].to_numpy(float), real_xyz)
        forward_bidir_xyz, forward_bidir_nis = forward_bidirectional_fusion(real_packet, g, sensor, args.i_id)

        uwb_xyz = g[["uwb_x_mm", "uwb_y_mm", "uwb_z_mm"]].to_numpy(float)
        uwb_row = make_row_from_positions(
            g,
            "REAL6_B0_A0_U4_P0_T1",
            uwb_xyz,
            "Pure UWB baseline A0/U4/P0/T1, copied into real-6axis smoke run.",
        )
        uwb_row = apply_row_metadata(
            uwb_row,
            information_use="causal-forward",
            coupling_mode="uwb_only_control",
            solver_family="T1",
            solver_detail="pure solved-UWB position baseline",
        )
        perfect_row = make_row_from_positions(
            g,
            "REAL6_X_A0_L0_I0_T11",
            perfect_xyz,
            "Perfect simulated 6-axis IMU-only strapdown diagnostic.",
        )
        perfect_row = apply_row_metadata(
            perfect_row,
            information_use="causal-forward",
            coupling_mode="imu_only_diagnostic",
            solver_family="T11",
            solver_detail="perfect IMU-only strapdown diagnostic",
        )
        real_row = make_row_from_positions(
            g,
            f"REAL6_X_A0_{args.l_id}_{args.i_id}_T11",
            real_xyz,
            f"Realistic simulated 6-axis {args.l_id}/{args.i_id} IMU-only strapdown diagnostic.",
        )
        real_row = apply_row_metadata(
            real_row,
            information_use="causal-forward",
            coupling_mode="imu_only_diagnostic",
            solver_family="T11",
            solver_detail="realistic IMU-only strapdown diagnostic",
        )
        fused_row = make_row_from_positions(
            g,
            f"REAL6_X_A0_U4_P0_{args.l_id}_{args.i_id}_T5LITE",
            fused_xyz,
            f"Realistic simulated 6-axis {args.l_id}/{args.i_id} causal UWB+IMU T5-lite smoke.",
        )
        fused_row = apply_row_metadata(
            fused_row,
            information_use="causal-forward",
            coupling_mode="uwb_corrects_imu",
            solver_family="T5LITE",
            solver_detail="causal IMU prediction with solved-UWB Kalman position update",
        )
        fused_row["uwb_innovation_nis"] = nis
        fused_row["uwb_innovation_nis_median"] = P1.pct(nis, 50)
        fused_row["uwb_innovation_nis_p95"] = P1.pct(nis, 95)
        fused_row["uwb_update_accept_rate"] = float(np.mean(np.isfinite(fused_xyz).all(axis=1)))
        fused_row["filter_divergence_count"] = int(np.sum(~np.isfinite(fused_xyz).all(axis=1)))
        forward_imu2uwb_row = make_row_from_positions(
            g,
            f"REAL6_X_A0_U4_P0_{args.l_id}_{args.i_id}_T2LITE_IMU2UWB",
            forward_imu2uwb_xyz,
            f"Realistic simulated 6-axis {args.l_id}/{args.i_id} causal IMU-corrects-UWB T2-lite smoke.",
        )
        forward_imu2uwb_row = apply_row_metadata(
            forward_imu2uwb_row,
            information_use="causal-forward",
            coupling_mode="imu_corrects_uwb",
            solver_family="T2LITE",
            solver_detail="causal solved-UWB trajectory corrected by IMU relative-motion prior",
        )
        forward_bidir_row = make_row_from_positions(
            g,
            f"REAL6_X_A0_U4_P0_{args.l_id}_{args.i_id}_T3LITE_BIDIR",
            forward_bidir_xyz,
            f"Realistic simulated 6-axis {args.l_id}/{args.i_id} causal bidirectional T3-lite smoke.",
        )
        forward_bidir_row = apply_row_metadata(
            forward_bidir_row,
            information_use="causal-forward",
            coupling_mode="bidirectional_joint",
            solver_family="T3LITE",
            solver_detail="causal IMU prediction gates UWB while accepted UWB corrects position/velocity drift proxy",
        )
        forward_bidir_row["uwb_innovation_nis"] = forward_bidir_nis
        forward_bidir_row["uwb_innovation_nis_median"] = P1.pct(forward_bidir_nis, 50)
        forward_bidir_row["uwb_innovation_nis_p95"] = P1.pct(forward_bidir_nis, 95)
        forward_bidir_row["uwb_update_accept_rate"] = float(np.mean(np.isfinite(forward_bidir_xyz).all(axis=1)))
        forward_bidir_row["filter_divergence_count"] = int(np.sum(~np.isfinite(forward_bidir_xyz).all(axis=1)))

        for df in [perfect_row, real_row]:
            drift = df[["x_mm", "y_mm", "z_mm"]].to_numpy(float) - df[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float)
            endpoint = drift[-1] if len(drift) else np.full(3, np.nan)
            duration = float(output_times[-1] - output_times[0]) if len(output_times) > 1 else float("nan")
            endpoint_3d = float(np.linalg.norm(endpoint))
            df["imu_only_endpoint_drift_3d_mm"] = endpoint_3d
            df["imu_only_endpoint_drift_xz_mm"] = float(math.hypot(endpoint[0], endpoint[2]))
            df["imu_only_drift_rate_3d_mm_s"] = endpoint_3d / duration if math.isfinite(duration) and duration > 0 else float("nan")

        real_packet_out = real_packet.copy()
        real_packet_out["capture_id"] = str(capture_id)
        real_packet_out["tag"] = str(tag)
        real_packet_out["L"] = args.l_id
        real_packet_out["I"] = args.i_id
        packet_chunks.append(real_packet_out)

        rows_uwb.append(uwb_row)
        rows_perfect_imu.append(perfect_row)
        rows_real_imu.append(real_row)
        rows_fused.append(fused_row)
        rows_forward_imu2uwb.append(forward_imu2uwb_row)
        rows_forward_bidir.append(forward_bidir_row)
        for mode in ["uwb_corrects_imu", "imu_corrects_uwb", "bidirectional_joint"]:
            if mode == "uwb_corrects_imu":
                anchor_xyz = fused_xyz
            elif mode == "imu_corrects_uwb":
                anchor_xyz = forward_imu2uwb_xyz
            else:
                anchor_xyz = forward_bidir_xyz
            session_tasks.append(
                {
                    "track": g,
                    "uwb_xyz_mm": uwb_xyz,
                    "imu_xyz_mm": real_xyz,
                    "anchor_xyz_mm": anchor_xyz,
                    "times_s": output_times,
                    "mode": mode,
                    "device": session_devices[len(session_tasks) % len(session_devices)],
                    "iters": args.session_iters,
                    "lr": args.session_lr,
                    "l_id": args.l_id,
                    "i_id": args.i_id,
                }
            )

    session_results: list[dict] = []
    grouped_session_tasks: dict[str, list[dict]] = {}
    for task in session_tasks:
        grouped_session_tasks.setdefault(task["device"], []).append(task)
    if len(grouped_session_tasks) > 1:
        import concurrent.futures
        import multiprocessing as mp

        ctx = mp.get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(max_workers=len(grouped_session_tasks), mp_context=ctx) as pool:
            futures = [pool.submit(run_session_task_batch, tasks) for tasks in grouped_session_tasks.values()]
            for fut in concurrent.futures.as_completed(futures):
                session_results.extend(fut.result())
    else:
        for tasks in grouped_session_tasks.values():
            session_results.extend(run_session_task_batch(tasks))

    rows_session_by_exp: dict[str, list[pd.DataFrame]] = {}
    for result in session_results:
        rows_session_by_exp.setdefault(str(result["experiment_id"]), []).append(result["row"])

    samples_by_exp = {
        "REAL6_B0_A0_U4_P0_T1": pd.concat(rows_uwb, ignore_index=True),
        "REAL6_X_A0_L0_I0_T11": pd.concat(rows_perfect_imu, ignore_index=True),
        f"REAL6_X_A0_{args.l_id}_{args.i_id}_T11": pd.concat(rows_real_imu, ignore_index=True),
        f"REAL6_X_A0_U4_P0_{args.l_id}_{args.i_id}_T2LITE_IMU2UWB": pd.concat(rows_forward_imu2uwb, ignore_index=True),
        f"REAL6_X_A0_U4_P0_{args.l_id}_{args.i_id}_T3LITE_BIDIR": pd.concat(rows_forward_bidir, ignore_index=True),
        f"REAL6_X_A0_U4_P0_{args.l_id}_{args.i_id}_T5LITE": pd.concat(rows_fused, ignore_index=True),
    }
    for exp in sorted(rows_session_by_exp):
        samples_by_exp[exp] = pd.concat(rows_session_by_exp[exp], ignore_index=True)
    summary, tracks = summarize_rows(samples_by_exp)
    summary["imu_packet_model"] = "explicit_ax_ay_az_gx_gy_gz"
    summary["orientation_source"] = args.orientation_mode
    summary["L"] = summary["experiment_id"].apply(lambda x: args.l_id if args.l_id in str(x) else ("L0" if "_L0_" in str(x) else ""))
    summary["I"] = summary["experiment_id"].apply(lambda x: args.i_id if args.i_id in str(x) else ("I0" if "_I0_" in str(x) else ""))
    metadata_cols = ["information_use", "coupling_mode", "solver_family", "solver_detail", "gpu_backend"]
    metadata_lookup = {}
    for exp, df in samples_by_exp.items():
        metadata_lookup[exp] = {}
        for col in metadata_cols:
            if col not in df.columns:
                metadata_lookup[exp][col] = ""
                continue
            vals = [str(v) for v in df[col].dropna().unique() if str(v)]
            metadata_lookup[exp][col] = "+".join(sorted(vals))
    for col in metadata_cols:
        summary[col] = summary["experiment_id"].map(lambda exp: metadata_lookup.get(str(exp), {}).get(col, ""))
    split_summary = split_accuracy_summary(samples_by_exp)
    split_summary = split_summary.merge(
        summary[["experiment_id", "legacy_deltaR_error_rms_mm"]],
        on="experiment_id",
        how="left",
    )

    summary.to_csv(run_dir / "tables" / "real_6axis_summary.csv", index=False)
    split_summary.to_csv(run_dir / "tables" / "real_6axis_split_accuracy.csv", index=False)
    tracks.to_csv(run_dir / "tables" / "real_6axis_track_metrics.csv", index=False)
    pd.concat(samples_by_exp.values(), ignore_index=True).to_csv(
        run_dir / "traces" / "real_6axis_samples.csv.gz", index=False, compression="gzip"
    )
    pd.concat(packet_chunks, ignore_index=True).to_csv(
        run_dir / "traces" / "real_6axis_imu_packets.csv.gz", index=False, compression="gzip"
    )

    plot_overlay_sheet(samples_by_exp, run_dir / "figs" / "contact_sheets" / "real_6axis_overlay_full.png")
    plot_overlay_sheet(
        samples_by_exp,
        run_dir / "figs" / "contact_sheets" / "real_6axis_overlay_selected.png",
        selected_captures=args.selected_captures,
    )
    method_pngs = plot_method_sheets(samples_by_exp, run_dir, selected_captures=args.selected_captures)
    triad_pngs = plot_triad_sheets(samples_by_exp, run_dir, selected_captures=args.selected_captures)

    manifest = {
        "run_id": args.run_id,
        "script": str(Path(__file__).resolve()),
        "status": "complete",
        "phase": "phase4_real_6axis_vertical_slice",
        "rows": list(samples_by_exp.keys()),
        "sensor": args.l_id,
        "imu_filter": args.i_id,
        "odr_hz": args.odr_hz,
        "orientation_source": args.orientation_mode,
        "session_solver_device_info": session_device_info,
        "session_iters": args.session_iters,
        "session_lr": args.session_lr,
        "input_baseline": "B0_A0_U4_P0_T1",
        "outputs": {
            "summary": "tables/real_6axis_summary.csv",
            "split_accuracy": "tables/real_6axis_split_accuracy.csv",
            "tracks": "tables/real_6axis_track_metrics.csv",
            "samples": "traces/real_6axis_samples.csv.gz",
            "imu_packets": "traces/real_6axis_imu_packets.csv.gz",
            "full_png": "figs/contact_sheets/real_6axis_overlay_full.png",
            "selected_png": "figs/contact_sheets/real_6axis_overlay_selected.png",
            "method_pngs": method_pngs,
            "triad_pngs": triad_pngs,
            "report": "reports/REAL_6AXIS_VERTICAL_SLICE.md",
        },
    }
    write_json(run_dir / "manifest.json", manifest)
    write_json(run_dir / "manifests" / "real_6axis_manifest.json", manifest)
    write_report(run_dir, args.run_id, summary, split_summary, sensor, args, method_pngs, triad_pngs)
    print(f"[real6axis] run_dir={run_dir}")
    print(summary[["experiment_id", "trackmedian_err3d_p50_mm", "trackmedian_err3d_p95_mm", "legacy_deltaR_error_rms_mm"]].to_string(index=False))


if __name__ == "__main__":
    main()
