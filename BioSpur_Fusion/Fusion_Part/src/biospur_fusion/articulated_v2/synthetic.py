from __future__ import annotations

import numpy as np

from .binding import FrozenMappingBinding
from .estimator import ArticulatedImuEstimator, ImuObservation
from .so3 import exp, geodesic, multiply, rotate


def oracle_specific_force(q_L0_sensor: np.ndarray, linear_accel_L0: np.ndarray, omega: np.ndarray, omega_dot: np.ndarray, lever_arm_sensor: np.ndarray) -> np.ndarray:
    """Independent closed-form observation oracle; no production residual helper."""
    r_L0 = rotate(q_L0_sensor, lever_arm_sensor)
    a_sensor = linear_accel_L0 + np.cross(omega_dot, r_L0) + np.cross(omega, np.cross(omega, r_L0))
    # Hand-derived quaternion inverse rotation, kept independent of production rotate.
    w, x, y, z = q_L0_sensor
    rotation = np.array([
        [1 - 2*(y*y + z*z), 2*(x*y + w*z), 2*(x*z - w*y)],
        [2*(x*y - w*z), 1 - 2*(x*x + z*z), 2*(y*z + w*x)],
        [2*(x*z + w*y), 2*(y*z - w*x), 1 - 2*(x*x + y*y)],
    ])
    return rotation @ (a_sensor - np.array([0.0, 0.0, -9.80665]))


def constant_rate_trial(binding: FrozenMappingBinding, config: dict, seed: int, duration_s: float = 2.0, gap: tuple[float, float] | None = None, realistic: bool = False) -> dict:
    rng = np.random.default_rng(seed)
    estimator = ArticulatedImuEstimator(binding, config)
    truth = {role: np.array([1.0, 0.0, 0.0, 0.0]) for role in binding.role_to_node()}
    rates = {role: rng.normal(0, 0.25, 3) for role in truth}
    if realistic:
        for role in truth:
            estimator.segments[role].q_L0_segment = exp(rng.normal(0, 0.08, 3))
    dt = 0.005
    sequence = {node: 0 for node in binding.node_to_role}
    seen = {node: False for node in binding.node_to_role}
    observations = []
    for step in range(int(duration_s / dt)):
        base_t = (step + 1) * dt
        for index, (node, role) in enumerate(sorted(binding.node_to_role.items())):
            t = base_t + index * 0.00007
            if seen[node]:
                truth[role] = multiply(truth[role], exp(rates[role] * dt))
            seen[node] = True
            gyro = rates[role] + (rng.normal(0, 0.008, 3) if realistic else 0.0)
            accel = oracle_specific_force(truth[role], np.zeros(3), rates[role], np.zeros(3), np.zeros(3))
            accel = accel + (rng.normal(0, 0.06, 3) if realistic else 0.0)
            if gap and gap[0] <= t <= gap[1] and index == 0:
                continue
            sequence[node] += 1
            observations.append(ImuObservation(node, t, sequence[node], gyro, accel, sample_age_s=(index % 5) * 0.001))
    observations.sort(key=lambda x: x.time_s)
    estimator.process(observations)
    estimator.assert_numerical_health()
    errors = [geodesic(estimator.segments[role].q_L0_segment, q) for role, q in truth.items()]
    return {"estimator": estimator, "errors_rad": errors, "median_rms_rad": float(np.median(errors)), "p95_rad": float(np.quantile(errors, .95))}


def monte_carlo(binding: FrozenMappingBinding, config: dict, trials: int = 200) -> dict:
    trial_errors = []
    cover = []
    gap_trace_ok = []
    for seed in range(trials):
        nominal = constant_rate_trial(binding, config, seed, realistic=True)
        gap = constant_rate_trial(binding, config, seed, gap=(0.7, 1.2), realistic=True)
        trial_errors.append(nominal["median_rms_rad"])
        pelvis = nominal["estimator"].segments["pelvis"]
        cone = 2.7954834829151074 * np.sqrt(max(np.linalg.eigvalsh(pelvis.covariance[:3, :3])))
        cover.append(float(nominal["errors_rad"][list(binding.role_to_node()).index("pelvis")] <= cone))
        node = sorted(binding.node_to_role)[0]
        role = binding.node_to_role[node]
        gap_trace_ok.append(float(np.trace(gap["estimator"].segments[role].covariance) >= np.trace(nominal["estimator"].segments[role].covariance)))
    # Trial is the independent coverage unit. Wilson interval, no time-sample pseudoreplication.
    p = float(np.mean(cover)); n = trials; z = 1.959963984540054
    den = 1 + z*z/n
    centre = (p + z*z/(2*n))/den
    half = z*np.sqrt(p*(1-p)/n + z*z/(4*n*n))/den
    return {
        "trials": trials, "independent_unit": "MONTE_CARLO_TRIAL",
        "normal_motion_median_rms_rad": float(np.median(trial_errors)),
        "normal_motion_p95_rad": float(np.quantile(trial_errors, .95)),
        "coverage_point": p, "coverage_wilson_95": [float(centre-half), float(centre+half)],
        "gap_additional_uncertainty_fraction": float(np.mean(gap_trace_ok)),
    }
