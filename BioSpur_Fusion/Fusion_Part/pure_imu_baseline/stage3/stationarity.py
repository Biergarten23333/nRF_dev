"""Causal native-IMU stationarity detection independent of pose and labels."""
from __future__ import annotations

import numpy as np

from pure_imu_baseline.decoder import physical
from pure_imu_baseline.orientation import session_times


def _rolling_mean(x: np.ndarray, width: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    c = np.r_[0.0, np.cumsum(x)]
    out = np.full(len(x), np.nan)
    if width <= len(x):
        out[width-1:] = (c[width:] - c[:-width]) / width
    return out


def detect_native(rows: np.ndarray, anchor_us: int, config: dict) -> dict[str, np.ndarray]:
    """Return a strictly backward-looking detector on native samples."""
    s = config["stationarity"]
    t = session_times(rows, anchor_us)
    acc, gyro = physical(rows)
    an = np.linalg.norm(acc, axis=1) / 9.80665
    gn2 = np.sum(gyro * gyro, axis=1)
    width = max(2, int(round(float(s["rolling_window_s"]) * 200.0)))
    gyro_rms = np.sqrt(_rolling_mean(gn2, width))
    gyro_mean = np.column_stack([_rolling_mean(gyro[:, j], width) for j in range(3)])
    gyro_mean2 = np.column_stack([_rolling_mean(gyro[:, j] ** 2, width) for j in range(3)])
    gyro_std = np.sqrt(np.maximum(0.0, np.sum(gyro_mean2 - gyro_mean ** 2, axis=1)))
    acc_err_rms = np.sqrt(_rolling_mean((an - 1.0) ** 2, width))
    acc_mean = _rolling_mean(an, width)
    acc_std = np.sqrt(np.maximum(0.0, _rolling_mean(an ** 2, width) - acc_mean ** 2))
    finite = np.isfinite(t) & np.all(np.isfinite(acc), axis=1) & np.all(np.isfinite(gyro), axis=1)
    nonsaturated = ((np.linalg.norm(gyro, axis=1) < float(s["gyro_saturation_rad_s"])) &
                    (np.max(np.abs(acc), axis=1) / 9.80665 < float(s["acc_saturation_g"])))
    contiguous = np.r_[False, (np.diff(t) > 0) & (np.diff(t) <= float(s["maximum_native_gap_s"]))]
    candidate = (finite & nonsaturated & contiguous &
                 (gyro_rms <= float(s["gyro_rms_max_rad_s"])) &
                 (gyro_std <= float(s["gyro_std_max_rad_s"])) &
                 (acc_err_rms <= float(s["acc_norm_error_rms_max_g"])) &
                 (acc_std <= float(s["acc_norm_std_max_g"])))
    minimum = max(1, int(round(float(s["minimum_duration_s"]) * 200.0)))
    stationary = np.zeros(len(candidate), dtype=bool)
    run = 0
    for k, ok in enumerate(candidate):
        run = run + 1 if ok else 0
        stationary[k] = run >= minimum
    return {"time_s": t, "stationary": stationary, "candidate": candidate,
            "gyro_rms_rad_s": gyro_rms, "gyro_std_rad_s": gyro_std,
            "acc_norm_error_rms_g": acc_err_rms, "acc_norm_std_g": acc_std,
            "finite": finite, "nonsaturated": nonsaturated}


def map_to_display(native: dict[str, np.ndarray], display_time_s: np.ndarray) -> np.ndarray:
    """Causal zero-order mapping; never uses the next native sample."""
    t = native["time_s"]
    idx = np.searchsorted(t, display_time_s, side="right") - 1
    valid = idx >= 0
    idx = np.clip(idx, 0, len(t) - 1)
    out = native["stationary"][idx] & valid
    out &= (display_time_s - t[idx]) <= 0.010
    return out
