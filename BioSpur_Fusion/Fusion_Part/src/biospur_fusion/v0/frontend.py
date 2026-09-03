"""Pinned VQF comparison and selected native-time VQF hybrid frontend."""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation
from vqf import VQF

from biospur_fusion.imu.frontend import run_q1_attitude
from biospur_fusion.root_r6a0.math3d import so3_exp

from .data import si_samples
from .math3d import matrix_to_quat_wxyz, quat_wxyz_to_matrix, rotation_angle, rz


def _world_z_twist(left: np.ndarray, right: np.ndarray) -> float:
    """World-z component of the principal relative SO(3) log.

    Inputs are generated proper rotations. Avoid the canonical public helper's
    repeated determinant/orthogonality validation in this per-sample loop.
    """
    return float(Rotation.from_matrix(left @ right.T).as_rotvec()[2])


@dataclass(frozen=True)
class AttitudeTimeline:
    time_ns: np.ndarray
    rotation_world_sensor: np.ndarray
    quaternion_wxyz: np.ndarray
    gyro_bias_rad_s: np.ndarray
    bias_sigma_rad_s: np.ndarray
    rest_detected: np.ndarray
    interval_id: np.ndarray
    accel_mps2: np.ndarray
    gyro_rad_s: np.ndarray
    native_dt_s: np.ndarray
    boundary_reason: np.ndarray
    audit: dict[str, Any]


def _interval_ids(times: np.ndarray, boot: np.ndarray, max_gap_ns: int) -> tuple[np.ndarray, np.ndarray]:
    dt = np.diff(times)
    if np.any(dt <= 0):
        raise ValueError("accepted native timestamps must be strictly increasing")
    boundary = (dt > max_gap_ns) | (boot[1:] != boot[:-1])
    ids = np.r_[0, np.cumsum(boundary)].astype(np.int32)
    reasons = np.full(len(times), "CONTINUOUS", dtype="U24")
    reasons[0] = "WINDOW_START"
    for index in np.flatnonzero(boundary) + 1:
        reasons[index] = "BOOT_RESET" if boot[index] != boot[index - 1] else "GAP_RESET"
    return ids, reasons


def run_vqf_native_hybrid(
    rows: np.ndarray,
    *,
    node_id: str,
    max_gap_ns: int,
    signed_axis: np.ndarray | None = None,
    use_vqf_bias_in_native_yaw: bool = True,
) -> AttitudeTimeline:
    """Run official VQF per valid block and close yaw propagation with native dt.

    VQF tilt, rest, bias, and bias uncertainty remain official outputs. VQF's
    required fixed sample period is measured as the median native period of
    each block. The active yaw/strapdown gauge is then propagated with every
    actual ``(t[i]-t[i-1])/1e9`` and is never composed across a gap or boot.
    """
    accepted = rows[rows["status"] == 1]
    times = accepted["global_time_ns"].astype(np.int64)
    boot = accepted["boot_epoch"].astype(np.int64)
    acc, gyr = si_samples(accepted, signed_axis)
    interval, boundary_reason = _interval_ids(times, boot, max_gap_ns)
    output_rotation = np.empty((len(times), 3, 3))
    output_bias = np.empty((len(times), 3))
    output_sigma = np.empty(len(times))
    output_rest = np.empty(len(times), bool)
    native_dt = np.r_[np.nan, np.diff(times) * 1e-9]
    block_audit = []
    previous_rotation: np.ndarray | None = None
    started = time.perf_counter()
    for block_id in np.unique(interval):
        index = np.flatnonzero(interval == block_id)
        if len(index) < 2:
            raise RuntimeError(f"{node_id}: VQF block has fewer than two rows")
        block_dt = np.diff(times[index]) * 1e-9
        observed_ts = float(np.median(block_dt))
        if not np.isfinite(observed_ts) or observed_ts <= 0:
            raise RuntimeError(f"{node_id}: invalid observed sampling period")
        vqf = VQF(observed_ts, magDistRejectionEnabled=False)
        reference = vqf.updateBatch(np.ascontiguousarray(gyr[index]), np.ascontiguousarray(acc[index]))
        reference_rotation = quat_wxyz_to_matrix(reference["quat6D"])
        active = np.empty_like(reference_rotation)
        if previous_rotation is None:
            active[0] = reference_rotation[0]
        else:
            # A yaw gauge transfer is not motion propagation across the missing
            # interval. Tilt and bias restart from current measurements. Use
            # the world-z twist of the relative SO(3), not an Euler/projection
            # heading that becomes singular when a sensor axis is vertical.
            yaw_twist = _world_z_twist(previous_rotation, reference_rotation[0])
            active[0] = rz(yaw_twist) @ reference_rotation[0]
        strap = active[0].copy()
        for local in range(1, len(index)):
            dt = float(times[index[local]] - times[index[local - 1]]) * 1e-9
            bias = reference["bias"][local - 1] if use_vqf_bias_in_native_yaw else np.zeros(3)
            strap = strap @ so3_exp((gyr[index[local - 1]] - bias) * dt)
            yaw_delta = _world_z_twist(strap, reference_rotation[local])
            active[local] = rz(yaw_delta) @ reference_rotation[local]
        previous_rotation = active[-1].copy()
        output_rotation[index] = active
        output_bias[index] = reference["bias"]
        output_sigma[index] = reference["biasSigma"]
        output_rest[index] = reference["restDetected"]
        block_audit.append({
            "interval_id": int(block_id),
            "rows": int(len(index)),
            "boot_epoch": int(boot[index[0]]),
            "first_time_ns": int(times[index[0]]),
            "last_time_ns": int(times[index[-1]]),
            "observed_median_ts_s": observed_ts,
            "native_dt_min_s": float(np.min(block_dt)),
            "native_dt_max_s": float(np.max(block_dt)),
            "native_dt_std_s": float(np.std(block_dt)),
        })
    elapsed = time.perf_counter() - started
    determinants = np.linalg.det(output_rotation)
    audit = {
        "node_id": node_id,
        "frontend": "VQF_2_0_1_TILT_REST_BIAS_PLUS_NATIVE_DT_YAW_STRAPDOWN",
        "official_vqf_fixed_period_source": "median accepted native dt within each valid interval",
        "native_dt_expression": "(global_time_ns[i]-global_time_ns[i-1])/1e9",
        "fixed_one_over_200_used": False,
        "vqf_bias_applied_to_native_yaw": use_vqf_bias_in_native_yaw,
        "magnetometer_used": False,
        "interval_count": int(interval[-1] + 1),
        "gap_or_boot_resets": int(np.count_nonzero(interval[1:] != interval[:-1])),
        "blocks": block_audit,
        "rest_fraction": float(np.mean(output_rest)),
        "final_bias_rad_s": output_bias[-1].tolist(),
        "final_bias_sigma_rad_s": float(output_sigma[-1]),
        "finite": bool(np.isfinite(output_rotation).all() and np.isfinite(output_bias).all()),
        "rotation_det_min": float(np.min(determinants)),
        "rotation_det_max": float(np.max(determinants)),
        "runtime_s": elapsed,
    }
    return AttitudeTimeline(
        times, output_rotation, matrix_to_quat_wxyz(output_rotation), output_bias,
        output_sigma, output_rest, interval, acc, gyr, native_dt, boundary_reason, audit,
    )


def _tilt_deviation_deg(rotation: np.ndarray) -> np.ndarray:
    gravity_sensor = np.einsum("nji,j->ni", rotation, np.array([0.0, 0.0, 1.0]))
    mean = np.mean(gravity_sensor, axis=0); mean /= np.linalg.norm(mean)
    return np.degrees(np.arccos(np.clip(gravity_sensor @ mean, -1.0, 1.0)))


def compare_q1_vqf(
    rows: np.ndarray,
    *,
    node_id: str,
    initial_start_ns: int,
    initial_end_ns: int,
    max_gap_ns: int,
) -> dict[str, Any]:
    """Equal-row Q1/VQF comparison; it makes no absolute-accuracy claim."""
    started = time.perf_counter()
    q1, q1_audit = run_q1_attitude(
        rows, node_id=node_id, initial_start_ns=initial_start_ns,
        initial_end_ns=initial_end_ns, analysis_end_ns=int(rows["global_time_ns"][-1]), decimation=1,
        max_gap_s=max_gap_ns * 1e-9,
    )
    q1_runtime = time.perf_counter() - started
    started = time.perf_counter()
    selected = run_vqf_native_hybrid(rows, node_id=node_id, max_gap_ns=max_gap_ns)
    vqf_runtime = time.perf_counter() - started
    q1_rotation = quat_wxyz_to_matrix(q1["q_wxyz"])
    q1_time = q1["global_time_ns"]
    match = np.searchsorted(selected.time_ns, q1_time)
    match = np.clip(match, 0, len(selected.time_ns) - 1)
    relative = np.degrees(rotation_angle(q1_rotation, selected.rotation_world_sensor[match]))
    still_q1 = (q1_time >= initial_start_ns) & (q1_time < initial_end_ns)
    still_vqf = (selected.time_ns >= initial_start_ns) & (selected.time_ns < initial_end_ns)
    q1_steps = np.degrees(rotation_angle(q1_rotation[:-1], q1_rotation[1:]))
    vqf_steps = np.degrees(rotation_angle(selected.rotation_world_sensor[:-1], selected.rotation_world_sensor[1:]))
    return {
        "schema": "biospur-fusion-v0-vqf-equal-input-comparison-v1",
        "node_id": node_id,
        "same_real_rows": True,
        "same_native_timestamps": True,
        "same_coordinate_interpretation": "RAW_REGISTER_REPRESENTATIVE_PLUS_X_PLUS_Y_PLUS_Z",
        "same_gap_reset_rule_ns": max_gap_ns,
        "identical_gap_reset_segmentation": (
            q1_audit.unused_gap_boundaries == selected.audit["gap_or_boot_resets"]
        ),
        "external_attitude_ground_truth": False,
        "q1": {
            "runtime_s": q1_runtime,
            "tilt_still_rms_deg": float(np.sqrt(np.mean(_tilt_deviation_deg(q1_rotation[still_q1]) ** 2))),
            "max_continuous_step_deg": float(np.max(q1_steps)),
            "rest_fraction": float(np.mean(q1["stationary"])),
            "final_gyro_bias_rad_s": q1["gyro_bias_rad_s"][-1].tolist(),
            "audit": q1_audit.__dict__,
            "fixed_one_over_200_diagnostic_present": True,
        },
        "vqf_hybrid": {
            "runtime_s": vqf_runtime,
            "tilt_still_rms_deg": float(np.sqrt(np.mean(_tilt_deviation_deg(selected.rotation_world_sensor[still_vqf]) ** 2))),
            "max_continuous_step_deg": float(np.max(vqf_steps[selected.interval_id[1:] == selected.interval_id[:-1]])),
            "rest_fraction": float(np.mean(selected.rest_detected)),
            "final_gyro_bias_rad_s": selected.gyro_bias_rad_s[-1].tolist(),
            "final_bias_sigma_rad_s": float(selected.bias_sigma_rad_s[-1]),
            "audit": selected.audit,
            "fixed_one_over_200_diagnostic_present": False,
        },
        "internal_orientation_difference_deg": {
            "median": float(np.median(relative)),
            "q95": float(np.quantile(relative, 0.95)),
            "maximum": float(np.max(relative)),
            "interpretation": "method disagreement only; not absolute error",
        },
        "selection": "VQF_2_0_1_NATIVE_TIME_HYBRID",
        "selection_reason": (
            "official VQF supplies executed rest/bias/uncertainty; native-time yaw closure removes "
            "the active Q1 motion-gate /200 assumption while retaining gap and boot segmentation"
        ),
    }
