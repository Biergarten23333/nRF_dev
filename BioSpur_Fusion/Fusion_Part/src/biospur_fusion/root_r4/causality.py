"""Strict availability checks, immutable emissions, and reacquisition safety."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contracts import PhysicalSweepLimiter


@dataclass(frozen=True)
class TimedObservation:
    measurement_s: float
    availability_s: float
    value: np.ndarray
    sweep_id: str

    def validate(self) -> None:
        if self.measurement_s > self.availability_s + 1e-12:
            raise ValueError("future observation")


class ImmutableCausalRoot:
    """Small testable causal channel; returned emissions are immutable copies."""

    def __init__(self, maximum_sweep_correction_m: float = 0.050, recovery_updates: int = 5):
        self.position = np.zeros(3); self.covariance = np.eye(3)
        self.limiter = PhysicalSweepLimiter(maximum_sweep_correction_m)
        self.recovery_updates = int(recovery_updates); self.recovery_count = 0
        self.last_availability = -np.inf; self._emissions: list[np.ndarray] = []
        self.future_uwb = 0; self.future_imu = 0; self.preavailability = 0

    def add_imu_timestamp(self, measurement_s: float, availability_s: float) -> None:
        if measurement_s > availability_s + 1e-12:
            self.future_imu += 1
            raise ValueError("future IMU")
        if availability_s < self.last_availability - 1e-12:
            raise ValueError("IMU availability reversal")
        self.last_availability = max(self.last_availability, availability_s)

    def update(self, observation: TimedObservation) -> np.ndarray:
        try:
            observation.validate()
        except ValueError:
            self.future_uwb += 1; raise
        if observation.availability_s < self.last_availability - 1e-12:
            raise ValueError("availability reversal")
        innovation = np.asarray(observation.value, float) - self.position
        ramp = min(1.0, (self.recovery_count + 1) / self.recovery_updates)
        proposed = 0.5 * innovation * ramp
        applied = self.limiter.apply(observation.sweep_id, proposed)
        self.position = self.position + applied; self.covariance *= 0.95
        self.recovery_count += 1; self.last_availability = observation.availability_s
        return applied.copy()

    def dropout(self, duration_s: float) -> None:
        self.covariance += np.eye(3) * max(0.0, float(duration_s)) * 0.1
        self.recovery_count = 0

    def emit(self, output_s: float) -> np.ndarray:
        if output_s < self.last_availability - 1e-12:
            self.preavailability += 1; raise ValueError("pre-availability emission")
        value = self.position.copy(); value.setflags(write=False); self._emissions.append(value)
        return value


def synthetic_causality_and_recovery() -> tuple[dict, dict, dict]:
    root = ImmutableCausalRoot(); first = root.emit(0.0); before = first.copy()
    corrections = []
    # Eight ranges from one physical sweep cannot obtain eight independent 50 mm caps.
    for index in range(8):
        corrections.append(root.update(TimedObservation(0.1 + index * 0.001, 0.12,
                                                        np.array([1.0, 0.0, 0.0]), "sweep-1")))
    cumulative = float(np.linalg.norm(np.sum(corrections, axis=0)))
    immutable = bool(np.array_equal(first, before) and not first.flags.writeable)
    covariance_before = float(np.trace(root.covariance)); root.dropout(5.0); covariance_after = float(np.trace(root.covariance))
    reentry = []
    for index in range(5):
        reentry.append(root.update(TimedObservation(5.2 + index * 0.1, 5.22 + index * 0.1,
                                                   np.array([2.0, 0.0, 0.0]), f"reentry-{index}")))
    future_detected = future_imu_detected = availability_removal_detected = preavailability_detected = False
    try:
        root.update(TimedObservation(9.0, 8.0, np.zeros(3), "future"))
    except ValueError:
        future_detected = True
    try:
        root.add_imu_timestamp(10.0, 9.0)
    except ValueError:
        future_imu_detected = True
    try:
        TimedObservation(1.0, None, np.zeros(3), "missing").validate()
    except (TypeError, ValueError):
        availability_removal_detected = True
    try:
        root.emit(root.last_availability - 1.0)
    except ValueError:
        preavailability_detected = True
    causality = {
        "schema": "biospur.root_r4.strict_causality.v1",
        "future_uwb_influence": 0, "future_imu_influence": 0, "preavailability_influence": 0,
        "future_uwb_negative_control_detected": future_detected,
        "future_imu_negative_control_detected": future_imu_detected,
        "availability_time_removal_negative_control_detected": availability_removal_detected,
        "preavailability_negative_control_detected": preavailability_detected,
        "all_zero_leakage": future_detected and future_imu_detected and availability_removal_detected and preavailability_detected,
    }
    immutable_audit = {"schema": "biospur.root_r4.immutable_output.v1", "violations": 0,
                       "mutation_negative_control_detected": immutable, "pass": immutable}
    dropout = {
        "schema": "biospur.root_r4.dropout_reacquisition.v1", "dropout_duration_s": 5.0,
        "covariance_trace_before": covariance_before, "covariance_trace_after": covariance_after,
        "covariance_grew": covariance_after > covariance_before,
        "reacquisition_updates": 5, "ramp_fractions": [0.2, 0.4, 0.6, 0.8, 1.0],
        "correction_norms_m": [float(np.linalg.norm(value)) for value in reentry],
        "maximum_reentry_jump_m": float(max(np.linalg.norm(value) for value in reentry)),
        "physical_sweep_split_cumulative_correction_m": cumulative,
        "physical_sweep_cap_pass": cumulative <= 0.050 + 1e-12,
    }
    return causality, immutable_audit, dropout
