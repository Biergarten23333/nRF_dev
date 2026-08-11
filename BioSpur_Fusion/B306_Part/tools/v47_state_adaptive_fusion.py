#!/usr/bin/env python3
"""Deterministic position/velocity Fusion with IMU motion evidence.

The main estimator intentionally does not rotate accelerometer vectors into the
current-room V4-io frame.  The required extrinsic is not identifiable in the
v47 capture.  IMU data is therefore used only for independent stationarity and
motion evidence; propagation is constant velocity with adaptive process noise.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np


STATES = ("INIT", "STATIONARY", "MOVING", "SETTLING")


def merge_event_order(control_times_s: np.ndarray,
                      uwb_times_s: np.ndarray) -> list[tuple[str, int, float]]:
    """Stable chronological merge; a UWB event is never quantized to IMU time."""
    control = np.asarray(control_times_s, dtype=float)
    uwb = np.asarray(uwb_times_s, dtype=float)
    if np.any(np.diff(control) < 0) or np.any(np.diff(uwb) < 0):
        raise ValueError("event stream timestamp reversal")
    out: list[tuple[str, int, float]] = []
    ci = ui = 0
    while ci < len(control) or ui < len(uwb):
        if ui < len(uwb) and (ci >= len(control) or uwb[ui] < control[ci]):
            out.append(("uwb", ui, float(uwb[ui])))
            ui += 1
        else:
            out.append(("control", ci, float(control[ci])))
            ci += 1
    return out


def wrap_safe_delta_us(current: int, previous: int, bits: int = 64) -> int:
    """Unsigned wrap-safe timestamp delta."""
    modulus = 1 << bits
    delta = (int(current) - int(previous)) % modulus
    if delta >= modulus // 2:
        raise ValueError("timestamp reversal or extreme delta")
    return delta


def cv_propagate(x: np.ndarray, covariance: np.ndarray, dt_s: float,
                 accel_sigma_mps2: float) -> tuple[np.ndarray, np.ndarray]:
    if not math.isfinite(dt_s) or dt_s < 0.0 or dt_s > 1.0:
        raise ValueError(f"invalid propagation dt {dt_s}")
    f = np.eye(6)
    f[:3, 3:] = np.eye(3) * dt_s
    q1 = np.array([[dt_s**3 / 3.0, dt_s**2 / 2.0],
                   [dt_s**2 / 2.0, dt_s]], dtype=float) * accel_sigma_mps2**2
    q = np.zeros((6, 6), dtype=float)
    for axis in range(3):
        q[axis, axis] = q1[0, 0]
        q[axis, axis + 3] = q1[0, 1]
        q[axis + 3, axis] = q1[1, 0]
        q[axis + 3, axis + 3] = q1[1, 1]
    out_x = f @ x
    out_p = f @ covariance @ f.T + q
    return out_x, 0.5 * (out_p + out_p.T)


def _joseph_update(x: np.ndarray, covariance: np.ndarray, innovation: np.ndarray,
                   h: np.ndarray, r: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    s = h @ covariance @ h.T + r
    gain = np.linalg.solve(s, h @ covariance).T
    corrected = x + gain @ innovation
    ident = np.eye(len(x))
    kh = gain @ h
    out_p = (ident - kh) @ covariance @ (ident - kh).T + gain @ r @ gain.T
    out_p = 0.5 * (out_p + out_p.T)
    nis = float(innovation @ np.linalg.solve(s, innovation))
    return corrected, out_p, nis


def position_update(x: np.ndarray, covariance: np.ndarray, measurement_m: np.ndarray,
                    r_m2: np.ndarray, nis_gate: float,
                    *, gate: bool = True) -> tuple[np.ndarray, np.ndarray, float, bool]:
    h = np.zeros((3, 6), dtype=float)
    h[:, :3] = np.eye(3)
    innovation = np.asarray(measurement_m, dtype=float) - x[:3]
    s = h @ covariance @ h.T + r_m2
    nis = float(innovation @ np.linalg.solve(s, innovation))
    if gate and nis > nis_gate:
        return x, covariance, nis, False
    out_x, out_p, _ = _joseph_update(x, covariance, innovation, h, r_m2)
    return out_x, out_p, nis, True


def zero_velocity_update(x: np.ndarray, covariance: np.ndarray,
                         sigma_mps: float) -> tuple[np.ndarray, np.ndarray, float]:
    h = np.zeros((3, 6), dtype=float)
    h[:, 3:] = np.eye(3)
    r = np.eye(3) * sigma_mps**2
    return _joseph_update(x, covariance, -x[3:], h, r)


def robust_platform(points: list[np.ndarray] | np.ndarray) -> tuple[np.ndarray, float]:
    values = np.asarray(points, dtype=float)
    if values.ndim != 2 or values.shape[1] != 3 or not len(values):
        raise ValueError("platform needs Nx3 points")
    center = np.median(values, axis=0)
    radial = np.linalg.norm(values - center, axis=1)
    return center, float(1.4826 * np.median(radial))


@dataclass(frozen=True)
class AdaptiveParameters:
    uwb_r_m2: np.ndarray
    gyro_rms_threshold_dps: float
    accel_dev_rms_threshold_g: float
    gyro_std_threshold_dps: float
    accel_std_threshold_g: float
    platform_stability_threshold_m: float
    platform_shift_threshold_m: float
    nis_gate: float = 16.266236
    exit_dwell_s: float = 0.75
    moving_quiet_dwell_s: float = 0.75
    settling_dwell_s: float = 2.0
    consensus_window_s: float = 2.0
    consensus_min_observations: int = 8
    consensus_update_period_s: float = 1.0
    stationary_accel_sigma_mps2: float = 0.03
    moving_accel_sigma_mps2: float = 1.0
    settling_accel_sigma_mps2: float = 0.25
    zupt_sigma_mps: float = 0.02
    stationary_speed_threshold_mps: float = 0.25


class StateAdaptiveFusion:
    """Four-state deterministic estimator driven by ordered control/UWB events."""

    def __init__(self, parameters: AdaptiveParameters, threshold_scale: float = 1.0):
        self.p = parameters
        self.scale = float(threshold_scale)
        self.state = "INIT"
        self.x = np.zeros(6, dtype=float)
        self.covariance = np.diag([1.0, 1.0, 1.0, .1, .1, .1])
        self.last_time_s: float | None = None
        self.recent: deque[tuple[float, np.ndarray]] = deque()
        self.transitions: list[dict] = []
        self.audit: list[dict] = []
        self.snapshots: list[dict] = []
        self.zupt_updates = 0
        self.zaru_updates = 0
        self.reinitializations = 0
        self.motion_evidence_elapsed = 0.0
        self.quiet_elapsed = 0.0
        self.settling_elapsed = 0.0
        self.last_control_s: float | None = None
        self.last_consensus_update_s = -math.inf
        self.lock_position: np.ndarray | None = None
        self.covariance_min_eigenvalue = math.inf
        self.covariance_max_asymmetry = 0.0
        self.negative_dt = 0
        self.extreme_dt = 0

    def _sigma(self) -> float:
        if self.state == "STATIONARY":
            return self.p.stationary_accel_sigma_mps2
        if self.state == "SETTLING":
            return self.p.settling_accel_sigma_mps2
        return self.p.moving_accel_sigma_mps2

    def _propagate(self, time_s: float) -> None:
        time_s = float(time_s)
        if self.last_time_s is None:
            self.last_time_s = time_s
            return
        dt = time_s - self.last_time_s
        if dt < 0:
            self.negative_dt += 1
            raise ValueError("event timestamp reversal")
        if dt > 1.0:
            self.extreme_dt += 1
            raise ValueError(f"extreme event dt {dt}")
        self.x, self.covariance = cv_propagate(self.x, self.covariance, dt, self._sigma())
        self.last_time_s = time_s
        self._check_covariance()

    def _check_covariance(self) -> None:
        if not np.isfinite(self.x).all() or not np.isfinite(self.covariance).all():
            raise FloatingPointError("non-finite state or covariance")
        asymmetry = float(np.max(np.abs(self.covariance - self.covariance.T)))
        eig = np.linalg.eigvalsh(0.5 * (self.covariance + self.covariance.T))
        self.covariance_max_asymmetry = max(self.covariance_max_asymmetry, asymmetry)
        self.covariance_min_eigenvalue = min(self.covariance_min_eigenvalue, float(eig[0]))
        if eig[0] < -1e-10:
            raise FloatingPointError(f"covariance not PSD: {eig[0]}")

    def _trim_recent(self, time_s: float) -> None:
        cutoff = time_s - self.p.consensus_window_s
        while self.recent and self.recent[0][0] < cutoff:
            self.recent.popleft()

    def _platform(self, time_s: float) -> tuple[np.ndarray | None, float]:
        self._trim_recent(time_s)
        if len(self.recent) < self.p.consensus_min_observations:
            return None, math.inf
        return robust_platform([point for _, point in self.recent])

    def _transition(self, time_s: float, new_state: str, reason: str) -> None:
        if new_state not in STATES or new_state == self.state:
            return
        old = self.state
        self.state = new_state
        self.transitions.append({"time_s": float(time_s), "from_state": old,
                                 "to_state": new_state, "reason": reason})
        self.motion_evidence_elapsed = 0.0
        self.quiet_elapsed = 0.0
        self.settling_elapsed = 0.0
        if new_state == "MOVING":
            # This is covariance adaptation, not a state reset.  It allows a
            # persistent new UWB platform to correct position/velocity.
            radius = self.p.platform_shift_threshold_m
            self.covariance[:3, :3] += np.eye(3) * radius**2
            self.covariance[3:, 3:] += np.eye(3) * self.p.moving_accel_sigma_mps2**2

    def process_uwb(self, time_s: float, measurement_m: np.ndarray | None,
                    *, status: str = "ok", record_index: int = -1) -> None:
        self._propagate(time_s)
        if measurement_m is None or status != "ok":
            category = "unavailable" if measurement_m is None else "invalid"
            self.audit.append({"record_index": record_index, "time_s": time_s,
                               "state": self.state, "category": category,
                               "nis": "", "update_applied": 0, "reason": status})
            return
        measurement = np.asarray(measurement_m, dtype=float)
        if measurement.shape != (3,) or not np.isfinite(measurement).all():
            self.audit.append({"record_index": record_index, "time_s": time_s,
                               "state": self.state, "category": "invalid",
                               "nis": "", "update_applied": 0,
                               "reason": "NONFINITE_OR_SHAPE"})
            return
        self.recent.append((float(time_s), measurement.copy()))
        self._trim_recent(time_s)
        if self.state == "INIT":
            category, nis, applied, reason = "accepted", 0.0, 0, "INIT_CONSENSUS_BUFFER"
        elif self.state == "STATIONARY":
            reference = self.lock_position if self.lock_position is not None else self.x[:3]
            innovation = measurement - reference
            nis = float(innovation @ np.linalg.solve(self.p.uwb_r_m2, innovation))
            applied = 0
            if nis <= self.p.nis_gate:
                category, reason = "accepted", "STATIONARY_CONSENSUS_BUFFER"
            else:
                category, reason = "rejected", "STATIONARY_NIS"
        else:
            self.x, self.covariance, nis, applied_bool = position_update(
                self.x, self.covariance, measurement, self.p.uwb_r_m2,
                self.p.nis_gate, gate=True,
            )
            applied = int(applied_bool)
            category = "accepted" if applied_bool else "rejected"
            reason = "MOVING_KALMAN" if applied_bool else "MOVING_NIS"
        self.audit.append({"record_index": record_index, "time_s": float(time_s),
                           "state": self.state, "category": category,
                           "nis": nis, "update_applied": applied, "reason": reason})
        self._check_covariance()
        self.snapshots.append({"time_s": float(time_s), "state": self.state,
                               "x_m": self.x.copy(), "velocity_mps": self.x[3:].copy(),
                               "cov_min_eig": float(np.linalg.eigvalsh(self.covariance)[0])})

    def process_control(self, time_s: float, features: dict[str, float],
                        *, sequence_advancing: bool = True) -> None:
        self._propagate(time_s)
        dt_control = 0.0 if self.last_control_s is None else float(time_s - self.last_control_s)
        self.last_control_s = float(time_s)
        thresholds = (
            self.p.gyro_rms_threshold_dps * self.scale,
            self.p.accel_dev_rms_threshold_g * self.scale,
            self.p.gyro_std_threshold_dps * self.scale,
            self.p.accel_std_threshold_g * self.scale,
        )
        values = (features["gyro_rms_dps"], features["accel_dev_rms_g"],
                  features["gyro_std_dps"], features["accel_std_g"])
        active_votes = sum(value > threshold for value, threshold in zip(values, thresholds))
        imu_quiet = bool(sequence_advancing and active_votes < 2)
        center, scatter = self._platform(time_s)
        platform_stable = center is not None and scatter <= self.p.platform_stability_threshold_m * self.scale

        if self.state == "INIT":
            if float(time_s) >= 1.0 and imu_quiet and platform_stable:
                self.x[:3] = center
                self.x[3:] = 0.0
                self.covariance = np.zeros((6, 6), dtype=float)
                self.covariance[:3, :3] = self.p.uwb_r_m2
                self.covariance[3:, 3:] = np.eye(3) * self.p.zupt_sigma_mps**2
                self.lock_position = center.copy()
                self._transition(time_s, "STATIONARY", "ROBUST_INITIAL_CONSENSUS")
            return

        shift = math.inf if center is None or self.lock_position is None else float(
            np.linalg.norm(center - self.lock_position)
        )
        # A moving point cloud is not expected to be a stable platform.  The
        # exit decision therefore uses a persistent robust-center shift while
        # IMU is active; platform stability is reserved for entry/relocking.
        motion_condition = (not imu_quiet) and center is not None and (
            shift > self.p.platform_shift_threshold_m * self.scale
        )
        if self.state == "STATIONARY":
            self.motion_evidence_elapsed = self.motion_evidence_elapsed + dt_control if motion_condition else 0.0
            if self.motion_evidence_elapsed >= self.p.exit_dwell_s:
                self._transition(time_s, "MOVING", "IMU_ACTIVE_AND_PERSISTENT_T4_SHIFT")
                return
            if imu_quiet:
                self.x, self.covariance, _ = zero_velocity_update(
                    self.x, self.covariance, self.p.zupt_sigma_mps
                )
                self.zupt_updates += 1
            if (imu_quiet and platform_stable and
                    shift <= self.p.platform_shift_threshold_m * self.scale and
                    time_s - self.last_consensus_update_s >=
                    self.p.consensus_update_period_s):
                consensus_r = self.p.uwb_r_m2 / max(1, min(len(self.recent), 16))
                self.x, self.covariance, _, _ = position_update(
                    self.x, self.covariance, center, consensus_r,
                    self.p.nis_gate, gate=False,
                )
                self.lock_position = self.x[:3].copy()
                self.last_consensus_update_s = float(time_s)
        elif self.state == "MOVING":
            quiet_condition = (imu_quiet and platform_stable and
                               np.linalg.norm(self.x[3:]) <=
                               self.p.stationary_speed_threshold_mps)
            self.quiet_elapsed = self.quiet_elapsed + dt_control if quiet_condition else 0.0
            if self.quiet_elapsed >= self.p.moving_quiet_dwell_s:
                self._transition(time_s, "SETTLING", "IMU_QUIET_AND_T4_STABLE")
        elif self.state == "SETTLING":
            if not imu_quiet or not platform_stable:
                self._transition(time_s, "MOVING", "SETTLING_INTERRUPTED")
                return
            self.settling_elapsed += dt_control
            self.x, self.covariance, _ = zero_velocity_update(
                self.x, self.covariance, self.p.zupt_sigma_mps
            )
            self.zupt_updates += 1
            if self.settling_elapsed >= self.p.settling_dwell_s and center is not None:
                consensus_r = self.p.uwb_r_m2 / max(1, min(len(self.recent), 16))
                self.x, self.covariance, _, _ = position_update(
                    self.x, self.covariance, center, consensus_r,
                    self.p.nis_gate, gate=False,
                )
                self.lock_position = self.x[:3].copy()
                self._transition(time_s, "STATIONARY", "SETTLED_ROBUST_CONSENSUS")
        self._check_covariance()


def require_vector_frame_binding(binding: dict) -> None:
    if binding.get("sensor_to_v4_transform_status") != "BOUND":
        raise ValueError("BLOCKED_FRAME_BINDING: sensor-to-V4 transform unavailable")
