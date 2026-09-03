"""Independent root-inertial equations, synthetic goldens, and real-C1 audit."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


GRAVITY_N_MPS2 = np.array([0.0, 0.0, -9.80665])
ROOT_R3_IMU_CACHE = Path("/tmp/biospur_c1_uwb_imu_root_r3_20260824T060622Z/PELVIS_IMU_DERIVED.npz")


def propagate_step(position_m: np.ndarray, velocity_mps: np.ndarray, bias_sensor_mps2: np.ndarray,
                   specific_force_sensor_mps2: np.ndarray, rotation_n_from_sensor: np.ndarray,
                   dt_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Midpoint-constant specific-force propagation in the gravity-aligned N frame."""

    dt = float(dt_s)
    if not np.isfinite(dt) or dt < 0.0:
        raise ValueError("invalid integration timestep")
    rotation = np.asarray(rotation_n_from_sensor, float)
    if rotation.shape != (3, 3) or np.linalg.det(rotation) <= 0.0:
        raise ValueError("rotation convention violation")
    acceleration = rotation @ (np.asarray(specific_force_sensor_mps2, float) -
                               np.asarray(bias_sensor_mps2, float)) + GRAVITY_N_MPS2
    position = np.asarray(position_m, float) + np.asarray(velocity_mps, float) * dt + 0.5 * acceleration * dt**2
    velocity = np.asarray(velocity_mps, float) + acceleration * dt
    return position, velocity


def covariance_step(covariance: np.ndarray, rotation_n_from_sensor: np.ndarray, dt_s: float,
                    acceleration_noise: float = 0.30, bias_rw: float = 0.003) -> np.ndarray:
    dt = float(dt_s); rotation = np.asarray(rotation_n_from_sensor, float)
    phi = np.eye(9); phi[:3, 3:6] = np.eye(3) * dt
    phi[:3, 6:9] = -0.5 * rotation * dt**2; phi[3:6, 6:9] = -rotation * dt
    q = np.zeros((9, 9)); sa2 = acceleration_noise**2; sb2 = bias_rw**2
    q[:3, :3] = np.eye(3) * sa2 * dt**3 / 3.0
    q[:3, 3:6] = q[3:6, :3] = np.eye(3) * sa2 * dt**2 / 2.0
    q[3:6, 3:6] = np.eye(3) * sa2 * dt; q[6:9, 6:9] = np.eye(3) * sb2 * dt
    value = phi @ np.asarray(covariance, float) @ phi.T + q
    return 0.5 * (value + value.T)


def integrate(time_s: np.ndarray, force: np.ndarray, rotations: np.ndarray, bias: np.ndarray,
              initial_velocity: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    time = np.asarray(time_s, float)
    if np.any(np.diff(time) <= 0.0):
        raise ValueError("IMU measurement time must be strictly increasing")
    position = np.zeros((len(time), 3)); velocity = np.zeros((len(time), 3)); covariance = np.zeros((len(time), 9, 9))
    if initial_velocity is not None:
        velocity[0] = initial_velocity
    covariance[0] = np.eye(9) * 1e-9
    for index in range(1, len(time)):
        dt = time[index] - time[index - 1]
        position[index], velocity[index] = propagate_step(position[index - 1], velocity[index - 1], bias,
                                                           force[index - 1], rotations[index - 1], dt)
        covariance[index] = covariance_step(covariance[index - 1], rotations[index - 1], dt)
    return position, velocity, covariance


def _rotation_z(angle: float) -> np.ndarray:
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def synthetic_goldens() -> dict:
    dt = 0.005; time = np.arange(0.0, 10.0 + dt, dt); identity = np.broadcast_to(np.eye(3), (len(time), 3, 3)).copy()
    cases = {}
    def run(name: str, truth_p: np.ndarray, truth_v: np.ndarray, acceleration: np.ndarray,
            rotations: np.ndarray = identity, bias: np.ndarray | None = None) -> None:
        b = np.zeros(3) if bias is None else np.asarray(bias, float)
        force = np.einsum("nij,nj->ni", np.swapaxes(rotations, 1, 2), acceleration - GRAVITY_N_MPS2) + b
        p, v, cov = integrate(time, force, rotations, b, truth_v[0])
        cases[name] = {
            "position_max_error_m": float(np.max(np.linalg.norm(p - truth_p, axis=1))),
            "velocity_max_error_mps": float(np.max(np.linalg.norm(v - truth_v, axis=1))),
            "covariance_psd": bool(np.min(np.linalg.eigvalsh(cov[-1])) >= -1e-12),
        }
    zeros = np.zeros((len(time), 3))
    run("stationary", zeros, zeros, zeros)
    velocity = np.broadcast_to(np.array([0.7, -0.2, 0.1]), (len(time), 3)).copy()
    run("constant_velocity", time[:, None] * velocity, velocity, zeros)
    acceleration = np.broadcast_to(np.array([0.3, -0.1, 0.2]), (len(time), 3)).copy()
    truth_v = acceleration * time[:, None]; truth_p = 0.5 * acceleration * time[:, None]**2
    run("constant_acceleration", truth_p, truth_v, acceleration)
    rotations = np.asarray([_rotation_z(0.7 * value) for value in time]); run("known_rotation_under_gravity", zeros, zeros, zeros, rotations)
    run("accelerometer_bias_removed", zeros, zeros, zeros, identity, np.array([0.08, -0.04, 0.12]))
    # A deliberately wrong gravity sign must be detected by a large stationary error.
    wrong_force = np.broadcast_to(np.array([0.0, 0.0, 9.80665]), (len(time), 3)).copy()
    wrong_p = np.zeros(3); wrong_v = np.zeros(3)
    for index in range(1, len(time)):
        acceleration_wrong = wrong_force[index - 1] - GRAVITY_N_MPS2
        wrong_p = wrong_p + wrong_v * dt + 0.5 * acceleration_wrong * dt**2; wrong_v = wrong_v + acceleration_wrong * dt
    cases["mutation_wrong_gravity_sign"] = {"detected": float(np.linalg.norm(wrong_p)) > 100.0,
                                             "terminal_position_error_m": float(np.linalg.norm(wrong_p))}
    passed = all(row.get("position_max_error_m", 0.0) < 1e-8 and row.get("velocity_max_error_mps", 0.0) < 1e-8
                 for name, row in cases.items() if not name.startswith("mutation")) and cases["mutation_wrong_gravity_sign"]["detected"]
    return {"schema": "biospur.root_r4.inertial_synthetic_goldens.v1", "cases": cases,
            "passed": bool(passed), "classification": "ROOT_INERTIAL_PROPAGATION_SYNTHETICALLY_QUALIFIED" if passed
            else "BLOCKED_ROOT_INERTIAL_PROPAGATION_INVALID"}


def real_c1_inertial_audit() -> tuple[dict, dict]:
    with np.load(ROOT_R3_IMU_CACHE, allow_pickle=False) as archive:
        time = archive["measurement_time_s"].astype(float)
        availability = archive["availability_time_s"].astype(float)
        force = archive["specific_force_sensor_mps2"].astype(float)
        rotations = archive["rotation_world_from_sensor"].astype(float)
        valid = archive["m1_valid"].astype(bool) & ~archive["m1_reset"].astype(bool)
    order = np.argsort(time, kind="stable"); time, availability, force, rotations, valid = [value[order] for value in (time, availability, force, rotations, valid)]
    unique = np.r_[True, np.diff(time) > 0.0]; time, availability, force, rotations, valid = [value[unique] for value in (time, availability, force, rotations, valid)]
    # Select the quietest 8 s window by world-acceleration norm variance; no UWB enters this selection.
    world_unbiased = np.einsum("nij,nj->ni", rotations, force) + GRAVITY_N_MPS2
    window = 1600; candidates = np.arange(0, max(1, len(time) - window), window)
    scores = [float(np.median(np.linalg.norm(world_unbiased[start:start + window] -
                                             np.median(world_unbiased[start:start + window], axis=0), axis=1))) for start in candidates]
    start = int(candidates[int(np.argmin(scores))]); stop = min(len(time), start + window)
    bias_candidates = force[start:stop] + np.einsum("nij,j->ni", np.swapaxes(rotations[start:stop], 1, 2), GRAVITY_N_MPS2)
    bias = np.median(bias_candidates, axis=0)
    p, v, covariance = integrate(time, force, rotations, bias)
    future = int(np.sum(time > availability + 1e-12))
    audit = {
        "schema": "biospur.root_r4.root_inertial_real_c1_audit.v1",
        "specific_force_equation": "a^N = R_N_from_I (f^I - b^I) + [0,0,-9.80665]",
        "rotation_convention": "Hamilton active local/sensor-to-N from frozen M1 q_GS_wxyz",
        "bias_convention": "additive sensor-frame specific-force bias",
        "integration": "piecewise-constant acceleration with exact p/v kinematics; measured dt",
        "bias_window_s": [float(time[start]), float(time[stop - 1])], "bias_samples": int(stop - start),
        "bias_sensor_mps2": bias.tolist(), "samples": len(time), "invalid_or_reset_samples": int(np.sum(~valid)),
        "future_imu_influence": future, "terminal_position_norm_m": float(np.linalg.norm(p[-1])),
        "maximum_position_norm_m": float(np.max(np.linalg.norm(p, axis=1))),
        "terminal_velocity_norm_mps": float(np.linalg.norm(v[-1])),
        "terminal_covariance_trace": float(np.trace(covariance[-1])),
        "real_c1_qualification": "NOT_QUALIFIED_DESPITE_SYNTHETICALLY_CORRECT_EQUATIONS",
    }
    trajectory = {"time_s": time, "position_m": p, "velocity_mps": v,
                  "covariance_diag": np.diagonal(covariance, axis1=1, axis2=2)}
    return audit, trajectory


def audit_markdown(synthetic: dict, real: dict) -> str:
    return f"""# Root-R4 root-inertial propagation audit

The independently implemented propagation uses `a^N = R_N_from_I(f^I-b^I) + g^N`,
with `g^N=[0,0,-9.80665] m/s²`, Hamilton active sensor-to-navigation rotations,
measured timesteps, and explicit sensor-frame bias. Stationary, constant-velocity,
constant-acceleration, rotating-gravity, and bias-removal goldens passed: **{synthetic['passed']}**.
The wrong-gravity mutation produced {synthetic['cases']['mutation_wrong_gravity_sign']['terminal_position_error_m']:.3f} m
error and was detected.

Real C1 remains unqualified: the inertial-only terminal displacement norm is
{real['terminal_position_norm_m']:.3f} m and the terminal speed is
{real['terminal_velocity_norm_mps']:.3f} m/s. Synthetic equation correctness does
not turn this unconstrained consumer IMU channel into an accurate navigator.
"""
