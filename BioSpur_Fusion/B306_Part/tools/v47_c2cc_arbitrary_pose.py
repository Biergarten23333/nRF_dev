#!/usr/bin/env python3
"""Deterministic primitives for BSFC2CC arbitrary-pose IMU calibration."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.linalg import expm
from scipy.optimize import least_squares

ACCEL_LSB_PER_G = 2048.0
GYRO_LSB_PER_DPS = 16.384

# Frozen before seeing calibration results.  The stationarity thresholds are
# deliberately much wider than the observed v47 tabletop noise, but narrow
# enough to reject handling and settling motion.
PREREGISTERED = {
    "stable_min_s": 15.0,
    "stable_target_s": 20.0,
    "window_s": 1.0,
    "minimum_samples_per_window": 150,
    "gyro_centered_rms_max_dps": 0.25,
    "gyro_axis_std_max_dps": 0.20,
    "accel_norm_std_max_g": 0.010,
    "gravity_direction_p95_max_deg": 1.0,
    "minimum_pairwise_direction_deg": 15.0,
    "coverage_covariance_min_eigenvalue": 0.10,
    "coverage_design_condition_max": 1.0e6,
    "model_loo_rmse_max_g": 0.030,
    "complex_model_min_relative_improvement": 0.20,
    "complex_model_min_absolute_improvement_g": 0.002,
    "heldout_rmse_max_g": 0.030,
    "heldout_max_abs_max_g": 0.060,
    "temperature_model_min_span_c": 2.0,
}

MODEL_ORDER = ("BIAS_ONLY", "DIAGONAL_SCALE", "FULL_SPD")


@dataclass
class StableDwell:
    """Causal dwell accumulator; any unsupported instant resets the island."""
    required_s: float = PREREGISTERED["stable_min_s"]
    start: float | None = None

    def update(self, monotonic: float, supported: bool) -> float:
        if not supported:
            self.start = None
            return 0.0
        if self.start is None:
            self.start = monotonic
        return monotonic - self.start


def pose_token_transition(state: str, token: str) -> str:
    """Pure mirror of the interactive gate, suitable for exhaustive tests."""
    if token == "STOP": return "STOPPED"
    if state == "WAIT_FIXED" and token == "FIXED": return "COLLECTING"
    if state == "WAIT_NEXT" and token == "NEXT": return "WAIT_FIXED"
    if state == "WAIT_NEXT" and token == "REPEAT": return "WAIT_FIXED"
    return state


def farthest_direction(existing, candidates: int = 20000) -> tuple[np.ndarray, float]:
    """Deterministic Fibonacci-sphere maximin coverage proposal."""
    d=np.asarray(existing,float);d=d/np.linalg.norm(d,axis=1)[:,None]
    k=np.arange(candidates);z=1-2*(k+.5)/candidates;phi=np.pi*(3-np.sqrt(5))*k;r=np.sqrt(1-z*z)
    c=np.c_[r*np.cos(phi),r*np.sin(phi),z]
    clearance=np.degrees(np.arccos(np.clip(c@d.T,-1,1))).min(axis=1);i=int(np.argmax(clearance))
    return c[i],float(clearance[i])


def parse_imu_samples(fields: dict[str, str], host_monotonic: float) -> list[dict]:
    """Parse one production decoded line without inventing physical axis names."""
    base = int(fields["base_us"], 0)
    seq = int(fields["seq"], 0)
    temp = int(fields["temp_raw"], 0) / 100.0
    out = []
    for i, text in enumerate(fields["samples"].split(";")):
        v = [int(x, 0) for x in text.split(",")]
        if len(v) != 7:
            raise ValueError("IMU tuple must contain dt,a0,a1,a2,g0,g1,g2")
        out.append({
            "host_monotonic": host_monotonic,
            "node_us": base + (v[0] & 0xFFFF),
            "seq": (seq + i) & 0xFFFF,
            "accel_raw": v[1:4],
            "gyro_raw": v[4:7],
            "accel_g": (np.asarray(v[1:4], dtype=float) / ACCEL_LSB_PER_G).tolist(),
            "gyro_dps": (np.asarray(v[4:7], dtype=float) / GYRO_LSB_PER_DPS).tolist(),
            "temperature_c": temp,
        })
    return out


def angular_distance_deg(a, b) -> float:
    a = np.asarray(a, float); b = np.asarray(b, float)
    return math.degrees(math.acos(float(np.clip(a @ b / np.linalg.norm(a) / np.linalg.norm(b), -1, 1))))


def stability_metrics(samples: list[dict]) -> dict:
    if not samples:
        return {"samples": 0, "stable": False}
    a = np.asarray([x["accel_g"] for x in samples], float)
    w = np.asarray([x["gyro_dps"] for x in samples], float)
    norm = np.linalg.norm(a, axis=1)
    unit = a / np.maximum(norm[:, None], 1e-12)
    center = np.median(unit, axis=0); center /= np.linalg.norm(center)
    angles = np.degrees(np.arccos(np.clip(unit @ center, -1, 1)))
    wc = w - np.median(w, axis=0)
    m = {
        "samples": len(samples),
        "accel_norm_std_g": float(np.std(norm)),
        "gyro_centered_rms_dps": float(np.sqrt(np.mean(np.sum(wc * wc, axis=1)))),
        "gyro_axis_std_max_dps": float(np.max(np.std(w, axis=0))),
        "gravity_direction_p95_deg": float(np.percentile(angles, 95)),
    }
    t = PREREGISTERED
    m["stable"] = bool(
        len(samples) >= t["minimum_samples_per_window"]
        and m["accel_norm_std_g"] <= t["accel_norm_std_max_g"]
        and m["gyro_centered_rms_dps"] <= t["gyro_centered_rms_max_dps"]
        and m["gyro_axis_std_max_dps"] <= t["gyro_axis_std_max_dps"]
        and m["gravity_direction_p95_deg"] <= t["gravity_direction_p95_max_deg"]
    )
    return m


def coverage_metrics(directions: list) -> dict:
    d = np.asarray(directions, float)
    d = d / np.linalg.norm(d, axis=1)[:, None]
    pair = [angular_distance_deg(d[i], d[j]) for i in range(len(d)) for j in range(i)]
    cov = d.T @ d / len(d)
    eig = np.linalg.eigvalsh(cov)
    # Sphere fit design [2a,1] is the relevant affine observability matrix.
    design = np.column_stack((2 * d, np.ones(len(d))))
    return {
        "count": len(d),
        "minimum_pairwise_angle_deg": float(min(pair)) if pair else None,
        "direction_covariance_eigenvalues": eig.tolist(),
        "direction_covariance_min_eigenvalue": float(eig[0]),
        "design_condition": float(np.linalg.cond(design)),
        "hemisphere_balance": np.mean(np.sign(d), axis=0).tolist(),
    }


def distinct_direction(candidate, accepted) -> tuple[bool, float | None]:
    if not accepted:
        return True, None
    angle = min(angular_distance_deg(candidate, x) for x in accepted)
    return angle >= PREREGISTERED["minimum_pairwise_direction_deg"], angle


def _sphere_center(a: np.ndarray) -> np.ndarray:
    # ||a-b||^2=r^2 -> 2a.b + c = ||a||^2
    X = np.column_stack((2 * a, np.ones(len(a))))
    return np.linalg.lstsq(X, np.sum(a * a, axis=1), rcond=None)[0][:3]


def _matrix_from_params(model: str, p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    b = p[:3]
    if model == "BIAS_ONLY": C = np.eye(3)
    elif model == "DIAGONAL_SCALE": C = np.diag(np.exp(p[3:6]))
    elif model == "FULL_SPD":
        S = np.array([[p[3], p[4], p[5]], [p[4], p[6], p[7]], [p[5], p[7], p[8]]])
        C = expm(S)
    else: raise ValueError(model)
    return b, C


def fit_model(accel_g: np.ndarray, model: str) -> dict:
    a = np.asarray(accel_g, float)
    if a.ndim != 2 or a.shape[1] != 3 or len(a) < 6: raise ValueError("need Nx3 accelerometer samples")
    b0 = _sphere_center(a)
    p0 = np.r_[b0, np.zeros({"BIAS_ONLY": 0, "DIAGONAL_SCALE": 3, "FULL_SPD": 6}[model])]
    def residual(p):
        b, C = _matrix_from_params(model, p)
        return np.linalg.norm((C @ (a - b).T).T, axis=1) - 1.0
    fit = least_squares(residual, p0, loss="huber", f_scale=0.005, max_nfev=3000)
    b, C = _matrix_from_params(model, fit.x); r = residual(fit.x)
    return {
        "model": model, "bias_g": b.tolist(), "correction_matrix": C.tolist(),
        "rmse_g": float(np.sqrt(np.mean(r*r))), "median_abs_g": float(np.median(np.abs(r))),
        "max_abs_g": float(np.max(np.abs(r))), "matrix_condition": float(np.linalg.cond(C)),
        "optimizer_success": bool(fit.success), "optimizer_cost": float(fit.cost),
    }


def apply_calibration(accel_g: np.ndarray, fit: dict) -> np.ndarray:
    a = np.asarray(accel_g, float); b = np.asarray(fit["bias_g"]); C = np.asarray(fit["correction_matrix"])
    return (C @ (a-b).T).T


def leave_one_pose_out(pose_arrays: list[np.ndarray], model: str) -> dict:
    errors = []
    for i in range(len(pose_arrays)):
        train = np.concatenate([x for j, x in enumerate(pose_arrays) if j != i])
        fit = fit_model(train, model); corrected = apply_calibration(pose_arrays[i], fit)
        errors.extend(np.linalg.norm(corrected, axis=1)-1)
    e = np.asarray(errors)
    return {"loo_rmse_g": float(np.sqrt(np.mean(e*e))), "loo_median_abs_g": float(np.median(np.abs(e))), "loo_max_abs_g": float(np.max(np.abs(e)))}


def fit_and_select(pose_arrays: list[np.ndarray]) -> dict:
    candidates = []
    pooled = np.concatenate(pose_arrays)
    for model in MODEL_ORDER:
        row = fit_model(pooled, model); row.update(leave_one_pose_out(pose_arrays, model)); candidates.append(row)
    selected = 0
    for i in range(1, len(candidates)):
        prev, cur = candidates[selected], candidates[i]
        improvement = prev["loo_rmse_g"] - cur["loo_rmse_g"]
        if (cur["loo_rmse_g"] <= PREREGISTERED["model_loo_rmse_max_g"]
                and improvement >= PREREGISTERED["complex_model_min_absolute_improvement_g"]
                and improvement / max(prev["loo_rmse_g"], 1e-12) >= PREREGISTERED["complex_model_min_relative_improvement"]):
            selected = i
    return {"selection_rule":"least complex; promote only for preregistered absolute and relative held-in LOO improvement", "selected_model":candidates[selected]["model"], "selected":candidates[selected], "candidates":candidates}


def heldout_metrics(pose_arrays: list[np.ndarray], fit: dict) -> dict:
    per=[]; all_e=[]; raw_e=[]
    for i,a in enumerate(pose_arrays,1):
        c=apply_calibration(a,fit);e=np.linalg.norm(c,axis=1)-1;raw=np.linalg.norm(a,axis=1)-1;all_e.extend(e);raw_e.extend(raw)
        per.append({"validation_pose":i,"samples":len(a),"uncalibrated_rmse_g":float(np.sqrt(np.mean(raw*raw))),"calibrated_rmse_g":float(np.sqrt(np.mean(e*e))),"median_abs_g":float(np.median(np.abs(e))),"max_abs_g":float(np.max(np.abs(e)))})
    e=np.asarray(all_e);raw=np.asarray(raw_e);rmse=float(np.sqrt(np.mean(e*e)));raw_rmse=float(np.sqrt(np.mean(raw*raw)));mx=float(np.max(np.abs(e)));absolute=raw_rmse-rmse;relative=absolute/max(raw_rmse,1e-12)
    return {"per_pose":per,"uncalibrated_rmse_g":raw_rmse,"rmse_g":rmse,"absolute_improvement_g":absolute,"relative_improvement":relative,"max_abs_g":mx,"pass":rmse<=PREREGISTERED["heldout_rmse_max_g"] and mx<=PREREGISTERED["heldout_max_abs_max_g"] and absolute>0}


def temperature_model(samples: list[dict]) -> dict:
    if not samples:
        return {"enabled":False,"reason":"NO_ACCEPTED_STATIONARY_SAMPLES","span_c":None}
    t=np.asarray([x["temperature_c"] for x in samples]);w=np.asarray([x["gyro_dps"] for x in samples]);span=float(np.ptp(t))
    if span < PREREGISTERED["temperature_model_min_span_c"]:
        return {"enabled":False,"reason":"INSUFFICIENT_TEMPERATURE_SPAN","span_c":span}
    X=np.column_stack((np.ones(len(t)),t-np.mean(t)));coef=np.linalg.lstsq(X,w,rcond=None)[0]
    return {"enabled":True,"span_c":span,"reference_c":float(np.mean(t)),"intercept_dps":coef[0].tolist(),"slope_dps_per_c":coef[1].tolist()}
