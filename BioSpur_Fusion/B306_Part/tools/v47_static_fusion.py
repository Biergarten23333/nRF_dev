#!/usr/bin/env python3
"""Offline real-data static inertial/ZUPT and UWB range-space replay.

This module is deliberately independent of hardware.  The navigation frame is
local ENU-like (z up), with yaw defined to be zero at initialization.  That
local frame is not promoted to the room frame until a capture-bound geometry
and board/body extrinsic are available.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np

G_MPS2 = 9.80665
T0_MASTER_MS = 77_860_264


def normalize_quaternion(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    norm = float(np.linalg.norm(q))
    if not math.isfinite(norm) or norm < 1e-15:
        raise ValueError("invalid quaternion")
    q = q / norm
    return -q if q[0] < 0 else q


def quaternion_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ], dtype=float)


def quaternion_from_two_vectors(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Return scalar-first q rotating source onto target."""
    a = np.asarray(source, dtype=float)
    b = np.asarray(target, dtype=float)
    a /= np.linalg.norm(a)
    b /= np.linalg.norm(b)
    dot = float(np.clip(np.dot(a, b), -1.0, 1.0))
    if dot < -1.0 + 1e-10:
        axis = np.cross(a, np.array([1.0, 0.0, 0.0]))
        if np.linalg.norm(axis) < 1e-8:
            axis = np.cross(a, np.array([0.0, 1.0, 0.0]))
        axis /= np.linalg.norm(axis)
        return np.r_[0.0, axis]
    return normalize_quaternion(np.r_[1.0 + dot, np.cross(a, b)])


def quaternion_to_matrix(q: np.ndarray) -> np.ndarray:
    w, x, y, z = normalize_quaternion(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def quaternion_step(q: np.ndarray, omega_rad_s: np.ndarray, dt_s: float) -> np.ndarray:
    rotation = np.asarray(omega_rad_s, dtype=float) * float(dt_s)
    angle = float(np.linalg.norm(rotation))
    if angle < 1e-12:
        dq = np.r_[1.0, 0.5 * rotation]
    else:
        dq = np.r_[math.cos(angle / 2), rotation * (math.sin(angle / 2) / angle)]
    return normalize_quaternion(quaternion_multiply(q, dq))


def euler_rpy_deg(q: np.ndarray) -> np.ndarray:
    r = quaternion_to_matrix(q)
    pitch = math.asin(float(np.clip(-r[2, 0], -1.0, 1.0)))
    roll = math.atan2(r[2, 1], r[2, 2])
    yaw = math.atan2(r[1, 0], r[0, 0])
    return np.degrees([roll, pitch, yaw])


def skew(v: np.ndarray) -> np.ndarray:
    x, y, z = v
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def range_jacobian(position_m: np.ndarray, anchor_m: np.ndarray) -> np.ndarray:
    d = np.asarray(position_m, dtype=float) - np.asarray(anchor_m, dtype=float)
    norm = float(np.linalg.norm(d))
    if norm < 1e-12:
        raise ValueError("range Jacobian undefined at anchor")
    return d / norm


def validate_bound_geometry_manifest(manifest: dict, capture_id: str) -> None:
    """Fail closed unless one unambiguous eight-anchor binding matches capture."""
    if manifest.get("binding_status") != "BOUND":
        raise ValueError("geometry is not bound")
    if manifest.get("capture_id") != capture_id:
        raise ValueError("geometry capture mismatch")
    if manifest.get("coordinate_unit") != "mm" or not manifest.get("coordinate_frame"):
        raise ValueError("geometry unit/frame missing")
    anchors = manifest.get("anchors")
    if not isinstance(anchors, list) or len(anchors) != 8:
        raise ValueError("exactly eight anchors required")
    ids = [int(a["id"]) for a in anchors]
    if sorted(ids) != list(range(8)) or len(set(ids)) != 8:
        raise ValueError("anchor ID collision or mismatch")
    for anchor in anchors:
        if any(key not in anchor for key in ("x_mm", "y_mm", "z_mm", "delay_mm")):
            raise ValueError("incomplete anchor geometry/delay")
    provenance = manifest.get("provenance", {})
    if not provenance.get("source_sha256") or not provenance.get("git_commit"):
        raise ValueError("geometry provenance missing")


def fit_node_clock(uwb: np.ndarray) -> tuple[float, float, float]:
    """Fit local B306 milliseconds to Master receipt milliseconds for labels only."""
    x = uwb["frame_us"].astype(float) / 1000.0
    y = uwb["master_ms"].astype(float)
    offset = float(np.median(y - x))
    slope = 1.0 + float(np.polyfit(x - x[0], y - (x + offset), 1)[0])
    intercept = float(np.median(y - slope * x))
    residual_p95 = float(np.quantile(np.abs(y - (slope * x + intercept)), 0.95))
    return slope, intercept, residual_p95


def local_to_t0_s(local_us: np.ndarray, clock: tuple[float, float, float]) -> np.ndarray:
    slope, intercept, _ = clock
    return (slope * np.asarray(local_us, dtype=float) / 1000.0 + intercept - T0_MASTER_MS) / 1000.0


def intervals_to_mask(times_s: np.ndarray, intervals: Iterable[tuple[float, float]]) -> np.ndarray:
    mask = np.zeros(len(times_s), dtype=bool)
    for start, end in intervals:
        mask |= (times_s >= float(start)) & (times_s < float(end))
    return mask


@dataclass(frozen=True)
class InertialConfig:
    fixed_dt_s: float | None = None
    initialize_gyro_bias: bool = True
    zupt: bool = False
    zupt_period_samples: int = 10
    covariance_period_samples: int = 10
    accel_noise_sigma_mps2: float = 0.12
    gyro_noise_sigma_rad_s: float = math.radians(0.12)
    gyro_bias_rw_sigma_rad_s2: float = math.radians(0.002)
    zupt_sigma_mps: float = 0.02


def _zupt_update(p: np.ndarray, v: np.ndarray, q: np.ndarray, bias: np.ndarray,
                 cov: np.ndarray, sigma: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    h = np.zeros((3, 12), dtype=float)
    h[:, 3:6] = np.eye(3)
    innovation = -v
    r = np.eye(3) * sigma * sigma
    s = h @ cov @ h.T + r
    k = np.linalg.solve(s, h @ cov).T
    correction = k @ innovation
    p = p + correction[:3]
    v = v + correction[3:6]
    dq = normalize_quaternion(np.r_[1.0, 0.5 * correction[6:9]])
    q = normalize_quaternion(quaternion_multiply(q, dq))
    bias = bias + correction[9:12]
    ident = np.eye(12)
    kh = k @ h
    cov = (ident - kh) @ cov @ (ident - kh).T + k @ r @ k.T
    cov = 0.5 * (cov + cov.T)
    nis = float(innovation @ np.linalg.solve(s, innovation))
    return p, v, q, bias, cov, nis


def replay_inertial(imu: np.ndarray, acc_g: np.ndarray, gyro_dps: np.ndarray,
                    times_t0_s: np.ndarray, stationary_mask: np.ndarray,
                    config: InertialConfig) -> dict:
    """Run all IMU samples and retain deterministic one-Hz state snapshots."""
    if len(imu) != len(times_t0_s) or len(imu) != len(stationary_mask):
        raise ValueError("input length mismatch")
    local_us = imu["b306_us"].astype(np.int64)
    if np.any(np.diff(local_us) <= 0):
        raise ValueError("timestamp reversal")
    if config.fixed_dt_s is not None and config.fixed_dt_s <= 0:
        raise ValueError("fixed dt must be positive")

    init = (times_t0_s >= 1.0) & (times_t0_s < 60.0)
    if np.sum(init) < 100:
        raise ValueError("insufficient initialization window")
    mean_acc = np.mean(acc_g[init], axis=0)
    q = quaternion_from_two_vectors(mean_acc, np.array([0.0, 0.0, 1.0]))
    initial_bias = np.radians(np.mean(gyro_dps[init], axis=0)) if config.initialize_gyro_bias else np.zeros(3)
    bias = initial_bias.copy()
    p = np.zeros(3)
    v = np.zeros(3)
    cov = np.diag(np.r_[np.full(3, 0.01**2), np.full(3, 0.05**2),
                        np.full(3, math.radians(1.0)**2), np.full(3, math.radians(0.05)**2)])

    seconds = np.arange(0, 1801, dtype=float)
    out = {
        "time_s": seconds.copy(), "position_m": np.full((len(seconds), 3), np.nan),
        "velocity_mps": np.full((len(seconds), 3), np.nan),
        "rpy_deg": np.full((len(seconds), 3), np.nan),
        "bias_rad_s": np.full((len(seconds), 3), np.nan),
        "cov_min_eig": np.full(len(seconds), np.nan), "cov_max_eig": np.full(len(seconds), np.nan),
    }
    next_snapshot = 0
    zupt_count = 0
    zupt_nis: list[float] = []
    zupt_count_by_second = np.zeros(len(seconds), dtype=np.int64)
    stationary_candidates = 0
    max_asymmetry = 0.0
    min_cov_eig = math.inf
    max_cov_eig = 0.0
    batch_boundary_jump = 0.0
    previous_p = p.copy()
    cov_elapsed = 0.0

    for index in range(1, len(imu)):
        actual_dt = (int(local_us[index]) - int(local_us[index - 1])) * 1e-6
        dt = config.fixed_dt_s if config.fixed_dt_s is not None else actual_dt
        if not math.isfinite(dt) or dt <= 0 or dt > 0.1:
            raise ValueError(f"invalid IMU dt {dt}")
        omega = np.radians(gyro_dps[index - 1]) - bias
        q = quaternion_step(q, omega, dt)
        rot = quaternion_to_matrix(q)
        specific_force = acc_g[index - 1] * G_MPS2
        acceleration = rot @ specific_force - np.array([0.0, 0.0, G_MPS2])
        p = p + v * dt + 0.5 * acceleration * dt * dt
        v = v + acceleration * dt
        cov_elapsed += dt

        if index % config.covariance_period_samples == 0:
            dc = cov_elapsed
            f = np.zeros((12, 12), dtype=float)
            f[:3, 3:6] = np.eye(3)
            f[3:6, 6:9] = -rot @ skew(specific_force)
            f[6:9, 9:12] = -np.eye(3)
            phi = np.eye(12) + f * dc
            qdiag = np.r_[np.full(3, 1e-12), np.full(3, config.accel_noise_sigma_mps2**2 * dc),
                           np.full(3, config.gyro_noise_sigma_rad_s**2 * dc),
                           np.full(3, config.gyro_bias_rw_sigma_rad_s2**2 * dc)]
            cov = phi @ cov @ phi.T + np.diag(qdiag)
            cov = 0.5 * (cov + cov.T)
            cov_elapsed = 0.0

        if stationary_mask[index]:
            stationary_candidates += 1
        if config.zupt and stationary_mask[index] and index % config.zupt_period_samples == 0:
            p, v, q, bias, cov, nis = _zupt_update(p, v, q, bias, cov, config.zupt_sigma_mps)
            zupt_count += 1
            zupt_nis.append(nis)
            sec = int(np.clip(math.floor(times_t0_s[index]), 0, len(seconds) - 1))
            zupt_count_by_second[sec] += 1

        if index > 1 and int(imu["delta_us"][index]) <= int(imu["delta_us"][index - 1]):
            batch_boundary_jump = max(batch_boundary_jump, float(np.linalg.norm(p - previous_p)))
        previous_p = p.copy()

        while next_snapshot < len(seconds) and times_t0_s[index] >= seconds[next_snapshot]:
            eig = np.linalg.eigvalsh(cov)
            asym = float(np.max(np.abs(cov - cov.T)))
            max_asymmetry = max(max_asymmetry, asym)
            min_cov_eig = min(min_cov_eig, float(eig[0]))
            max_cov_eig = max(max_cov_eig, float(eig[-1]))
            out["position_m"][next_snapshot] = p
            out["velocity_mps"][next_snapshot] = v
            out["rpy_deg"][next_snapshot] = euler_rpy_deg(q)
            out["bias_rad_s"][next_snapshot] = bias
            out["cov_min_eig"][next_snapshot] = eig[0]
            out["cov_max_eig"][next_snapshot] = eig[-1]
            next_snapshot += 1

    valid = np.isfinite(out["position_m"][:, 0])
    out.update({
        "initial_gyro_bias_rad_s": initial_bias,
        "final_gyro_bias_rad_s": bias,
        "initial_gravity_body_g": mean_acc,
        "stationary_candidate_samples": stationary_candidates,
        "zupt_updates": zupt_count,
        "zupt_nis": np.asarray(zupt_nis),
        "zupt_count_by_second": zupt_count_by_second,
        "covariance_min_eigenvalue": min_cov_eig,
        "covariance_max_eigenvalue": max_cov_eig,
        "covariance_max_asymmetry": max_asymmetry,
        "batch_boundary_max_position_step_m": batch_boundary_jump,
        "finite": bool(np.isfinite(p).all() and np.isfinite(v).all() and np.isfinite(q).all()
                       and np.isfinite(bias).all() and np.isfinite(cov).all()),
        "timestamp_reversals": int(np.sum(np.diff(local_us) <= 0)),
        "valid_snapshot_mask": valid,
    })
    return out


@dataclass(frozen=True)
class RangeConfig:
    r_mode: str = "per_link"
    gate_enabled: bool = True
    nis_gate: float = 10.827566
    process_sigma_mm_per_sqrt_s: float = 3.0
    sigma_floor_mm: float = 20.0
    collect_audit: bool = True


def replay_range_space(uwb: np.ndarray, imu_local_us: np.ndarray,
                       baseline_mask: np.ndarray, per_link_sigma_mm: np.ndarray,
                       uniform_sigma_mm: float, config: RangeConfig) -> tuple[list[dict], dict]:
    """Independent scalar range Kalman plumbing when room geometry is unavailable."""
    if len(uwb) != len(baseline_mask):
        raise ValueError("baseline mask length mismatch")
    if np.any(np.diff(uwb["strobe_us"].astype(np.int64)) <= 0):
        raise ValueError("UWB timestamp reversal")
    means = np.full(8, np.nan)
    variances = np.full(8, np.nan)
    for slot in range(8):
        valid = baseline_mask & ((uwb["valid_mask"] & (1 << slot)) != 0)
        values = uwb["range_mm"][valid, slot].astype(float)
        if not len(values):
            raise ValueError(f"no baseline observations for slot {slot}")
        means[slot] = float(np.median(values))
        sigma = uniform_sigma_mm if config.r_mode == "uniform" else float(per_link_sigma_mm[slot])
        variances[slot] = max(sigma, config.sigma_floor_mm) ** 2

    initial_means = means.copy()
    rows: list[dict] = []
    valid_total = invalid_total = accepted = rejected = 0
    residuals: list[float] = []
    nis_values: list[float] = []
    second_residual_sq = np.zeros(1801, dtype=float)
    second_nis_sum = np.zeros(1801, dtype=float)
    second_counts = np.zeros(1801, dtype=np.int64)
    stamps = uwb["strobe_us"].astype(np.int64)
    insert_left = np.searchsorted(imu_local_us, stamps, side="right") - 1
    insertion_ok = (insert_left >= 0) & (insert_left < len(imu_local_us) - 1)
    safe_left = np.clip(insert_left, 0, len(imu_local_us) - 2)
    insertion_ok &= (imu_local_us[safe_left] <= stamps) & (stamps < imu_local_us[safe_left + 1])
    insertion_errors = int(np.sum(~insertion_ok))
    previous_us = int(stamps[0])
    for record_index, record in enumerate(uwb):
        stamp = int(record["strobe_us"])
        left = int(insert_left[record_index])
        dt = max(0.0, (stamp - previous_us) * 1e-6)
        previous_us = stamp
        for slot in range(8):
            aid = int(record["anchor_id"][slot])
            is_valid = bool(int(record["valid_mask"]) & (1 << slot))
            if not is_valid:
                invalid_total += 1
                if config.collect_audit:
                    common = {
                        "record_index": record_index, "anchor_slot": slot, "anchor_id": aid,
                        "strobe_us": stamp, "master_ms": int(record["master_ms"]),
                        "t0_s": (int(record["master_ms"]) - T0_MASTER_MS) / 1000.0,
                        "imu_left_index": left, "quality": int(record["quality"][slot]),
                        "rank": int(record["rank"][slot]), "cfo_ppm_q8": int(record["cfo_ppm_q8"][slot]),
                        "t_round_us": int(record["t_round_us"][slot]),
                    }
                    rows.append({**common, "valid": 0, "accepted": 0, "residual_mm": "",
                                 "innovation_variance_mm2": "", "nis": "", "gate": config.nis_gate,
                                 "rejection_reason": "INVALID_MASK"})
                continue
            valid_total += 1
            measurement = float(record["range_mm"][slot])
            variances[slot] += config.process_sigma_mm_per_sqrt_s**2 * dt
            sigma = uniform_sigma_mm if config.r_mode == "uniform" else float(per_link_sigma_mm[slot])
            r = max(sigma, config.sigma_floor_mm) ** 2
            residual = measurement - means[slot]
            innovation_variance = variances[slot] + r
            nis = residual * residual / innovation_variance
            take = (not config.gate_enabled) or nis <= config.nis_gate
            if take:
                gain = variances[slot] / innovation_variance
                means[slot] += gain * residual
                variances[slot] = (1.0 - gain) * variances[slot]
                accepted += 1
                reason = ""
            else:
                rejected += 1
                reason = "NIS_GATE"
            residuals.append(residual)
            nis_values.append(nis)
            sec = int(np.clip(math.floor((int(record["master_ms"]) - T0_MASTER_MS) / 1000.0), 0, 1800))
            second_residual_sq[sec] += residual * residual
            second_nis_sum[sec] += nis
            second_counts[sec] += 1
            if not take and config.collect_audit:
                common = {
                    "record_index": record_index, "anchor_slot": slot, "anchor_id": aid,
                    "strobe_us": stamp, "master_ms": int(record["master_ms"]),
                    "t0_s": (int(record["master_ms"]) - T0_MASTER_MS) / 1000.0,
                    "imu_left_index": left, "quality": int(record["quality"][slot]),
                    "rank": int(record["rank"][slot]), "cfo_ppm_q8": int(record["cfo_ppm_q8"][slot]),
                    "t_round_us": int(record["t_round_us"][slot]),
                }
                rows.append({**common, "valid": 1, "accepted": 0,
                             "residual_mm": residual, "innovation_variance_mm2": innovation_variance,
                             "nis": nis, "gate": config.nis_gate, "rejection_reason": reason})

    summary = {
        "records": len(uwb), "slots_total": len(uwb) * 8, "valid": valid_total,
        "invalid": invalid_total, "accepted": accepted, "rejected": rejected,
        "accounting_closed": valid_total + invalid_total == len(uwb) * 8 and accepted + rejected == valid_total,
        "insertion_errors": insertion_errors,
        "residual_rms_mm": float(np.sqrt(np.mean(np.square(residuals)))),
        "residual_p95_abs_mm": float(np.quantile(np.abs(residuals), 0.95)),
        "nis_median": float(np.median(nis_values)), "nis_p95": float(np.quantile(nis_values, 0.95)),
        "final_range_state_mm": means.tolist(), "final_variance_mm2": variances.tolist(),
        "state_changed": bool(np.any(np.abs(means - initial_means) > 1e-9)),
        "residual_rms_by_second": np.sqrt(np.divide(second_residual_sq, second_counts,
                                                     out=np.full(1801, np.nan), where=second_counts > 0)),
        "nis_mean_by_second": np.divide(second_nis_sum, second_counts,
                                         out=np.full(1801, np.nan), where=second_counts > 0),
    }
    return rows, summary
