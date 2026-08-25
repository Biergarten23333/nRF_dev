"""Independent synthetic qualification gates A--T for Root-R6A1A."""
from __future__ import annotations

import hashlib

import numpy as np

from biospur_fusion.imu.preintegration import (
    ImuSample,
    NativeTimePreintegrator,
    NoiseParameters,
    PreintegrationStatus,
    PreintegratorConfig,
    so3_log,
)


NODES = (
    "BSFEC35", "BSFB165", "BSFAA61", "BSF1120", "BSF31CC",
    "BSFC2CC", "BSF44AD", "BSF3C79", "BSF6C53", "BSF8BC4",
)


def _noise(scale: float = 1.0, provenance: str = "ROOT_R6A1A_SYNTHETIC_TEST_ONLY") -> NoiseParameters:
    return NoiseParameters(0.020 * scale, 0.002 * scale, 0.0002 * scale, 0.00002 * scale, provenance)


def _preintegrator(scale: float = 1.0) -> NativeTimePreintegrator:
    return NativeTimePreintegrator({node: _noise(scale) for node in NODES})


def _samples(
    times_s: np.ndarray,
    accel,
    gyro,
    *,
    node: str = "BSFEC35",
    boot: int = 4,
) -> tuple[ImuSample, ...]:
    result = []
    for index, time_s in enumerate(times_s):
        a = accel(time_s) if callable(accel) else accel
        w = gyro(time_s) if callable(gyro) else gyro
        result.append(ImuSample(node, int(round(float(time_s) * 1e9)), boot, np.asarray(a, float), np.asarray(w, float)))
    return tuple(result)


def _rotation_error(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(so3_log(a.T @ b)))


def _record(gates: dict, letter: str, name: str, passed: bool, **metrics) -> None:
    gates[letter] = {"name": name, "pass": bool(passed), "metrics": metrics}


def run_qualification() -> dict:
    gates: dict[str, dict] = {}
    pi = _preintegrator()

    # A: exact zero-motion identity on native irregular time.
    times = np.array([0.0, 0.003, 0.0085, 0.014, 0.019])
    result = pi.integrate(_samples(times, np.zeros(3), np.zeros(3)))
    error = max(_rotation_error(np.eye(3), result.delta_rotation), float(np.linalg.norm(result.delta_velocity)), float(np.linalg.norm(result.delta_position)))
    _record(gates, "A", "zero_motion_identity", result.valid and error < 1e-15, maximum_error=error, duration_s=result.duration_s)

    # B: a stationary accelerometer observes +g specific force; no gravity is
    # hidden in preintegration.  A downstream -g navigation term cancels it.
    times = np.linspace(0.0, 1.0, 201)
    result = pi.integrate(_samples(times, np.array([0.0, 0.0, 9.80665]), np.zeros(3)))
    dv_expected = np.array([0.0, 0.0, 9.80665])
    dp_expected = np.array([0.0, 0.0, 4.903325])
    error = max(float(np.linalg.norm(result.delta_velocity - dv_expected)), float(np.linalg.norm(result.delta_position - dp_expected)))
    downstream_net_dv = float(np.linalg.norm(result.delta_velocity + np.array([0.0, 0.0, -9.80665])))
    _record(gates, "B", "specific_force_gravity_convention", result.valid and error < 2e-12 and downstream_net_dv < 2e-12,
            maximum_error=error, downstream_stationary_net_delta_v_mps=downstream_net_dv)

    # C: constant body angular velocity.
    omega = np.array([0.0, 0.0, np.pi / 2.0])
    result = pi.integrate(_samples(times, np.zeros(3), omega))
    expected = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    error = _rotation_error(expected, result.delta_rotation)
    _record(gates, "C", "constant_angular_rate", result.valid and error < 2e-12, rotation_error_rad=error)

    # D: constant specific force gives exact ZOH kinematics.
    acceleration = np.array([2.0, -0.5, 0.25])
    result = pi.integrate(_samples(times, acceleration, np.zeros(3)))
    error_v = float(np.linalg.norm(result.delta_velocity - acceleration))
    error_p = float(np.linalg.norm(result.delta_position - 0.5 * acceleration))
    _record(gates, "D", "constant_specific_force", result.valid and max(error_v, error_p) < 2e-12,
            delta_velocity_error=error_v, delta_position_error=error_p)

    # E: coupled rotation/specific-force trajectory against continuous truth.
    times_e = np.linspace(0.0, 1.0, 2001)
    w = np.pi / 2.0
    a = 1.5
    result = pi.integrate(_samples(times_e, np.array([a, 0.0, 0.0]), np.array([0.0, 0.0, w])))
    expected_v = np.array([a * np.sin(w) / w, a * (1.0 - np.cos(w)) / w, 0.0])
    expected_p = np.array([a * (1.0 - np.cos(w)) / w**2, a * (1.0 / w - np.sin(w) / w**2), 0.0])
    error_v = float(np.linalg.norm(result.delta_velocity - expected_v))
    error_p = float(np.linalg.norm(result.delta_position - expected_p))
    _record(gates, "E", "coupled_rotating_specific_force", result.valid and error_v < 8e-4 and error_p < 5e-4,
            delta_velocity_error=error_v, delta_position_error=error_p)

    # F: every actual dt contributes; a nominal-rate substitute differs.
    times_f = np.array([0.0, 0.003, 0.009, 0.013, 0.022])
    actual = pi.integrate(_samples(times_f, np.array([1.0, 0.0, 0.0]), np.zeros(3)))
    nominal_times = np.arange(len(times_f), dtype=float) * 0.005
    nominal = pi.integrate(_samples(nominal_times, np.array([1.0, 0.0, 0.0]), np.zeros(3)))
    difference = float(np.linalg.norm(actual.delta_velocity - nominal.delta_velocity))
    _record(gates, "F", "native_irregular_dt_not_nominal_rate", actual.valid and abs(actual.duration_s - 0.022) < 1e-15 and difference > 1e-3,
            native_duration_s=actual.duration_s, nominal_substitute_duration_s=nominal.duration_s, delta_velocity_difference=difference)

    # G: interval splitting and SE_2(3) composition recover the full result.
    times_g = np.array([0.0, 0.004, 0.009, 0.015, 0.020, 0.027, 0.033])
    accel_fn = lambda t: np.array([0.7 + 0.2 * t, -0.3 + 0.1 * t, 0.2])
    gyro_fn = lambda t: np.array([0.1, -0.05 + 0.02 * t, 0.3])
    all_samples = _samples(times_g, accel_fn, gyro_fn)
    full = pi.integrate(all_samples)
    split_index = 3
    first = pi.integrate(all_samples[:split_index + 1])
    split_origin_ns = all_samples[split_index].global_time_ns
    second_samples = tuple(
        ImuSample(
            sample.node_id,
            sample.global_time_ns - split_origin_ns,
            sample.boot_epoch,
            sample.accel_mps2,
            sample.gyro_rad_s,
        )
        for sample in all_samples[split_index:]
    )
    second = pi.integrate(second_samples)
    duration_second = second.duration_s
    composed_r = first.delta_rotation @ second.delta_rotation
    composed_v = first.delta_velocity + first.delta_rotation @ second.delta_velocity
    composed_p = first.delta_position + first.delta_velocity * duration_second + first.delta_rotation @ second.delta_position
    errors = (_rotation_error(full.delta_rotation, composed_r), float(np.linalg.norm(full.delta_velocity - composed_v)), float(np.linalg.norm(full.delta_position - composed_p)))
    _record(gates, "G", "split_interval_composition", all(x < 2e-12 for x in errors),
            rotation_error_rad=errors[0], velocity_error=errors[1], position_error=errors[2])

    # H: convergence against a much finer direct integration.
    accel_h = lambda t: np.array([0.4 * np.cos(1.3 * t), 0.3 * np.sin(0.7 * t), -0.1 + 0.05 * t])
    gyro_h = lambda t: np.array([0.2 * np.sin(0.9 * t), -0.15 * np.cos(0.4 * t), 0.25 + 0.05 * t])
    coarse = pi.integrate(_samples(np.arange(0.0, 0.5000001, 0.002), accel_h, gyro_h))
    fine = pi.integrate(_samples(np.arange(0.0, 0.5000001, 0.0002), accel_h, gyro_h))
    errors = (_rotation_error(fine.delta_rotation, coarse.delta_rotation), float(np.linalg.norm(fine.delta_velocity - coarse.delta_velocity)), float(np.linalg.norm(fine.delta_position - coarse.delta_position)))
    _record(gates, "H", "high_resolution_direct_integration", max(errors) < 5e-4,
            rotation_error_rad=errors[0], velocity_error=errors[1], position_error=errors[2])

    # I/J: stored first-order bias Jacobians against central differences.
    times_j = np.linspace(0.0, 0.6, 121)
    samples_j = _samples(times_j, lambda t: np.array([0.6 + 0.1 * t, -0.4, 9.7]), lambda t: np.array([0.15, -0.1 + 0.03 * t, 0.25]))
    baseline = pi.integrate(samples_j, gyro_bias_rad_s=np.array([0.01, -0.015, 0.005]), accel_bias_mps2=np.array([0.04, -0.03, 0.02]))
    epsilon = 1e-6
    fd_r = np.zeros((3, 3)); fd_v_bg = np.zeros((3, 3)); fd_p_bg = np.zeros((3, 3))
    for axis in range(3):
        step = np.zeros(3); step[axis] = epsilon
        plus = pi.integrate(samples_j, gyro_bias_rad_s=baseline.reference_gyro_bias_rad_s + step, accel_bias_mps2=baseline.reference_accel_bias_mps2)
        minus = pi.integrate(samples_j, gyro_bias_rad_s=baseline.reference_gyro_bias_rad_s - step, accel_bias_mps2=baseline.reference_accel_bias_mps2)
        fd_r[:, axis] = (so3_log(baseline.delta_rotation.T @ plus.delta_rotation) - so3_log(baseline.delta_rotation.T @ minus.delta_rotation)) / (2.0 * epsilon)
        fd_v_bg[:, axis] = (plus.delta_velocity - minus.delta_velocity) / (2.0 * epsilon)
        fd_p_bg[:, axis] = (plus.delta_position - minus.delta_position) / (2.0 * epsilon)
    gyro_errors = (
        float(np.max(np.abs(fd_r - baseline.jacobian_rotation_gyro_bias))),
        float(np.max(np.abs(fd_v_bg - baseline.jacobian_velocity_gyro_bias))),
        float(np.max(np.abs(fd_p_bg - baseline.jacobian_position_gyro_bias))),
    )
    _record(gates, "I", "gyro_bias_jacobians_finite_difference", max(gyro_errors) < 3e-6,
            rotation_max_abs_error=gyro_errors[0], velocity_max_abs_error=gyro_errors[1], position_max_abs_error=gyro_errors[2])

    fd_v_ba = np.zeros((3, 3)); fd_p_ba = np.zeros((3, 3))
    for axis in range(3):
        step = np.zeros(3); step[axis] = epsilon
        plus = pi.integrate(samples_j, gyro_bias_rad_s=baseline.reference_gyro_bias_rad_s, accel_bias_mps2=baseline.reference_accel_bias_mps2 + step)
        minus = pi.integrate(samples_j, gyro_bias_rad_s=baseline.reference_gyro_bias_rad_s, accel_bias_mps2=baseline.reference_accel_bias_mps2 - step)
        fd_v_ba[:, axis] = (plus.delta_velocity - minus.delta_velocity) / (2.0 * epsilon)
        fd_p_ba[:, axis] = (plus.delta_position - minus.delta_position) / (2.0 * epsilon)
    accel_errors = (
        float(np.max(np.abs(fd_v_ba - baseline.jacobian_velocity_accel_bias))),
        float(np.max(np.abs(fd_p_ba - baseline.jacobian_position_accel_bias))),
    )
    _record(gates, "J", "accel_bias_jacobians_finite_difference", max(accel_errors) < 3e-8,
            velocity_max_abs_error=accel_errors[0], position_max_abs_error=accel_errors[1])

    # K: covariance is symmetric positive semidefinite.
    symmetry = float(np.max(np.abs(baseline.covariance - baseline.covariance.T)))
    minimum_eigenvalue = float(np.min(np.linalg.eigvalsh(baseline.covariance)))
    _record(gates, "K", "covariance_symmetric_psd", symmetry < 1e-14 and minimum_eigenvalue > -1e-14,
            symmetry_max_abs_error=symmetry, minimum_eigenvalue=minimum_eigenvalue)

    # L: uncertainty grows with duration and with all density scales.
    shorter_samples = _samples(np.linspace(0.0, 0.25, 51), np.array([0.0, 0.0, 9.80665]), np.zeros(3))
    longer_samples = _samples(np.linspace(0.0, 1.0, 201), np.array([0.0, 0.0, 9.80665]), np.zeros(3))
    trace_short = float(np.trace(pi.integrate(shorter_samples).covariance))
    trace_long = float(np.trace(pi.integrate(longer_samples).covariance))
    trace_high = float(np.trace(_preintegrator(3.0).integrate(longer_samples).covariance))
    _record(gates, "L", "covariance_duration_and_noise_growth", 0.0 < trace_short < trace_long < trace_high,
            short_trace=trace_short, long_trace=trace_long, high_noise_trace=trace_high)

    # M--R: explicit hard boundaries and typed statuses.
    duplicate = pi.integrate(_samples(np.array([0.0, 0.005, 0.005]), np.zeros(3), np.zeros(3)))
    _record(gates, "M", "duplicate_timestamp_rejected", duplicate.status is PreintegrationStatus.DUPLICATE_TIMESTAMP,
            status=duplicate.status.value)
    reversal = pi.integrate(_samples(np.array([0.0, 0.006, 0.004]), np.zeros(3), np.zeros(3)))
    _record(gates, "N", "time_reversal_rejected", reversal.status is PreintegrationStatus.TIME_REVERSAL,
            status=reversal.status.value)
    bounded = pi.integrate(_samples(np.array([0.0, 0.005, 0.015, 0.020]), np.zeros(3), np.zeros(3)))
    _record(gates, "O", "bounded_gap_accounted", bounded.valid and bounded.bounded_gap_count == 1,
            status=bounded.status.value, bounded_gap_count=bounded.bounded_gap_count, max_dt_s=bounded.max_dt_s)
    excessive = pi.integrate(_samples(np.array([0.0, 0.005, 0.026]), np.zeros(3), np.zeros(3)))
    _record(gates, "P", "excessive_gap_rejected", excessive.status is PreintegrationStatus.GAP_EXCEEDS_ENVELOPE,
            status=excessive.status.value)
    reset_samples = list(_samples(np.array([0.0, 0.005, 0.010]), np.zeros(3), np.zeros(3)))
    reset_samples[-1] = ImuSample(reset_samples[-1].node_id, reset_samples[-1].global_time_ns, 5, np.zeros(3), np.zeros(3))
    reset = pi.integrate(reset_samples)
    _record(gates, "Q", "boot_epoch_boundary_rejected", reset.status is PreintegrationStatus.BOOT_EPOCH_CHANGE,
            status=reset.status.value)
    rail_samples = list(_samples(np.array([0.0, 0.005]), np.zeros(3), np.zeros(3)))
    rail_samples[0] = ImuSample("BSFEC35", 0, 4, np.zeros(3), np.zeros(3), acc_raw=(32767, 0, 0), gyro_raw=(0, 0, 0))
    saturation = pi.integrate(rail_samples)
    nan_samples = list(_samples(np.array([0.0, 0.005]), np.zeros(3), np.zeros(3)))
    nan_samples[0] = ImuSample("BSFEC35", 0, 4, np.array([np.nan, 0.0, 0.0]), np.zeros(3))
    nonfinite_nan = pi.integrate(nan_samples)
    inf_samples = list(_samples(np.array([0.0, 0.005]), np.zeros(3), np.zeros(3)))
    inf_samples[0] = ImuSample("BSFEC35", 0, 4, np.zeros(3), np.array([0.0, np.inf, 0.0]))
    nonfinite_inf = pi.integrate(inf_samples)
    rejected_samples = list(_samples(np.array([0.0, 0.005]), np.zeros(3), np.zeros(3)))
    rejected_samples[0] = ImuSample("BSFEC35", 0, 4, np.zeros(3), np.zeros(3), accepted=False)
    rejected = pi.integrate(rejected_samples)
    passed = (saturation.status is PreintegrationStatus.SATURATION
              and nonfinite_nan.status is PreintegrationStatus.NONFINITE
              and nonfinite_inf.status is PreintegrationStatus.NONFINITE
              and rejected.status is PreintegrationStatus.INVALID_SAMPLE_STATUS)
    _record(gates, "R", "saturation_nonfinite_and_status_rejected", passed,
            saturation_status=saturation.status.value, nan_status=nonfinite_nan.status.value,
            infinity_status=nonfinite_inf.status.value, rejected_ledger_status=rejected.status.value)

    # S: deterministic bitwise replay.
    replay_a = pi.integrate(samples_j)
    replay_b = pi.integrate(samples_j)
    payload_a = b"".join(value.tobytes() for value in (
        replay_a.delta_rotation, replay_a.delta_velocity, replay_a.delta_position,
        replay_a.jacobian_rotation_gyro_bias, replay_a.jacobian_velocity_gyro_bias,
        replay_a.jacobian_velocity_accel_bias, replay_a.jacobian_position_gyro_bias,
        replay_a.jacobian_position_accel_bias, replay_a.covariance,
    ))
    payload_b = b"".join(value.tobytes() for value in (
        replay_b.delta_rotation, replay_b.delta_velocity, replay_b.delta_position,
        replay_b.jacobian_rotation_gyro_bias, replay_b.jacobian_velocity_gyro_bias,
        replay_b.jacobian_velocity_accel_bias, replay_b.jacobian_position_gyro_bias,
        replay_b.jacobian_position_accel_bias, replay_b.covariance,
    ))
    digest = hashlib.sha256(payload_a).hexdigest()
    _record(gates, "S", "deterministic_bitwise_replay", payload_a == payload_b,
            sha256=digest, bytes=len(payload_a))

    # T: asynchronous streams retain independent epochs, counts, and duration.
    streams = {}
    for node_index, node in enumerate(NODES):
        dts = 0.0045 + 0.0001 * node_index + 0.00015 * np.sin(np.arange(80) * (0.17 + node_index * 0.01))
        node_times = np.r_[0.00031 * node_index, 0.00031 * node_index + np.cumsum(dts)]
        streams[node] = _samples(node_times, np.array([0.1, -0.2, 9.7]), np.array([0.01, 0.02, -0.03]), node=node, boot=2 + node_index)
    async_results = pi.integrate_async(streams)
    durations = [round(async_results[node].duration_s, 12) for node in NODES]
    starts = [async_results[node].start_time_ns for node in NODES]
    passed = (set(async_results) == set(NODES) and all(value.valid for value in async_results.values())
              and all(value.sample_count == 81 for value in async_results.values())
              and len(set(durations)) == len(NODES) and len(set(starts)) == len(NODES))
    _record(gates, "T", "ten_asynchronous_node_streams", passed,
            valid_nodes=sum(value.valid for value in async_results.values()), unique_durations=len(set(durations)),
            unique_start_times=len(set(starts)), sample_count_per_node=81)

    expected = list("ABCDEFGHIJKLMNOPQRST")
    return {
        "schema": "biospur-root-r6a1a-synthetic-gates-v1",
        "gate_order": expected,
        "gates": gates,
        "summary": {
            "expected_gate_count": 20,
            "executed_gate_count": len(gates),
            "passed_gate_count": sum(gates[key]["pass"] for key in expected),
            "all_pass": len(gates) == 20 and all(gates[key]["pass"] for key in expected),
            "synthetic_noise_provenance": "ROOT_R6A1A_SYNTHETIC_TEST_ONLY",
        },
    }
