"""Q1 attitude/preintegration frontend with token-window initialization."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

import numpy as np

from .q1 import FrameBinding, MotionVetoGate, Q1Parameters, Q1T4ESKF

G = 9.80665


@dataclass(frozen=True)
class ImuFrontendAudit:
    node_id: str
    initialized_samples: int
    propagated_samples: int
    gravity_attempts: int
    gravity_accepted: int
    gravity_rejected: int
    gravity_motion_ineligible: int
    zupt_updates: int
    finite: bool
    cholesky_failures: int
    max_quaternion_norm_error: float
    min_covariance_eigenvalue: float
    yaw_gauge: str
    spatial_acceleration_enabled: bool
    duplicate_timestamps_rejected: int
    source_order_reorders: int
    unused_gap_boundaries: int


def run_q1_attitude(imu: np.ndarray, *, node_id: str, initial_start_ns: int, initial_end_ns: int,
                    analysis_end_ns: int, decimation: int = 4) -> tuple[np.ndarray, ImuFrontendAudit]:
    valid = (imu["status"] == 1) & (imu["global_time_ns"] <= analysis_end_ns)
    initial = valid & (imu["global_time_ns"] >= initial_start_ns) & (imu["global_time_ns"] <= initial_end_ns)
    if int(initial.sum()) < 200:
        raise ValueError(f"{node_id}: token-labelled initial-still has insufficient IMU samples")
    accel = imu["acc_raw"].astype(float) / 2048.0 * G
    gyro = np.deg2rad(imu["gyro_raw"].astype(float) / 16.384)
    binding = FrameBinding(
        provenance="TOKEN_LABELLED_INITIAL_STILL_ATTITUDE_ONLY",
        yaw_gauge="ARBITRARY_ZERO_WITH_PI_UNCERTAINTY",
        v4_navigation_rotation_valid=False,
        spatial_dynamics_enabled=False,
    )
    q1 = Q1T4ESKF(Q1Parameters(covariance_period_samples=24), binding)
    q1.initialize_from_stationary(np.mean(accel[initial], axis=0), np.mean(gyro[initial], axis=0))
    gate = MotionVetoGate(); rows = []
    raw_indices = np.flatnonzero(valid & (imu["global_time_ns"] >= initial_start_ns))
    source_order_reorders = int(np.sum(np.diff(imu["global_time_ns"][raw_indices]) < 0))
    indices = raw_indices[np.argsort(imu["global_time_ns"][raw_indices], kind="stable")]
    times = imu["global_time_ns"][indices]
    unique = np.r_[True, np.diff(times) > 0]
    duplicate_timestamps = int((~unique).sum())
    indices = indices[unique]
    gap_boundaries = 0
    for ordinal, index in enumerate(indices):
        time_s = int(imu[index]["global_time_ns"]) / 1e9
        if q1.last_timestamp_s is not None and time_s - q1.last_timestamp_s > q1.parameters.max_dt_s:
            # Do not fabricate samples across an unsupported gap. Attitude is
            # carried to an explicit boundary and propagation resumes after it.
            q1.last_timestamp_s = time_s
            gap_boundaries += 1
        else:
            q1.propagate(time_s, accel[index], gyro[index])
        gyro_dps = float(np.linalg.norm(np.rad2deg(gyro[index])))
        accel_dev = abs(float(np.linalg.norm(accel[index])) / G - 1.0)
        motion = gate.update(time_s, gyro_rms_dps=gyro_dps, gyro_angle_deg=gyro_dps / 200.0,
                             accel_deviation_g=accel_dev, candidate_stable=gyro_dps < .2 and accel_dev < .01,
                             velocity_mps=float(np.linalg.norm(q1.v)))
        if ordinal % 10 == 0:
            decision = q1.gravity_update_causal(accel[index], motion_state=motion)
            if decision.accepted and motion == "STATIONARY":
                q1.zupt_update()
        if ordinal % decimation == 0:
            rows.append((int(imu[index]["global_time_ns"]), q1.q.copy(), q1.b_g.copy(), q1.b_a.copy(),
                         motion == "STATIONARY"))
    timeline = np.asarray(rows, dtype=np.dtype([
        ("global_time_ns", "<i8"), ("q_wxyz", "<f8", (4,)), ("gyro_bias_rad_s", "<f8", (3,)),
        ("accel_bias_mps2", "<f8", (3,)), ("stationary", "?"),
    ]))
    audit = ImuFrontendAudit(
        node_id, int(initial.sum()), len(indices), q1.gravity_update_attempts, q1.gravity_updates,
        q1.gravity_update_rejections, q1.gravity_motion_ineligible, q1.zupt_updates,
        bool(np.isfinite(timeline["q_wxyz"]).all()), q1.cholesky_failures,
        q1.max_quaternion_norm_error, q1.min_covariance_eigenvalue,
        binding.yaw_gauge, False, duplicate_timestamps, source_order_reorders, gap_boundaries,
    )
    return timeline, audit


def audits_as_json(audits: Mapping[str, ImuFrontendAudit]) -> dict:
    return {node: asdict(audit) for node, audit in sorted(audits.items())}
