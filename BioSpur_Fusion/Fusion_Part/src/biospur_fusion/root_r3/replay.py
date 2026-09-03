"""Deterministic baseline runners for real C1 and quarantined diagnostics."""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

import numpy as np

from .estimator import CausalDelayedRootFilter, RootFilterConfig
from .models import ImuSample, PositionObservation, RootOutput, RootState, SystemMode


def initial_state(time_s: float, position_m: np.ndarray, bias_sensor_mps2: np.ndarray | None = None,
                  *, position_sigma_m: float = 0.50, velocity_sigma_mps: float = 0.50,
                  bias_sigma_mps2: float = 0.10) -> RootState:
    vector = np.zeros(9); vector[:3] = np.asarray(position_m, float)
    if bias_sensor_mps2 is not None:
        vector[6:9] = np.asarray(bias_sensor_mps2, float)
    diagonal = np.r_[np.full(3, position_sigma_m**2), np.full(3, velocity_sigma_mps**2),
                     np.full(3, bias_sigma_mps2**2)]
    return RootState(float(time_s), vector, np.diag(diagonal))


def _arrays(outputs: list[RootOutput], decisions: list, observations: list[PositionObservation]) -> dict:
    count = len(outputs)
    return {
        "time_s": np.asarray([row.output_time_s for row in outputs], float),
        "root_m": np.asarray([row.root_position_m for row in outputs], np.float32).reshape(count, 3),
        "velocity_mps": np.asarray([row.root_velocity_mps for row in outputs], np.float32).reshape(count, 3),
        "covariance_diag_m2": np.asarray([np.diag(row.root_covariance_m2) for row in outputs], np.float32).reshape(count, 3),
        "measurement_age_s": np.asarray([np.nan if row.measurement_age_s is None else row.measurement_age_s for row in outputs], np.float32),
        "mode": np.asarray([row.active_mode.value for row in outputs], dtype="U24"),
        "accepted": np.asarray([bool(decision.accepted) for decision in decisions], bool),
        "reason": np.asarray([decision.reason for decision in decisions], dtype="U40"),
        "nis": np.asarray([np.nan if decision.nis is None else decision.nis for decision in decisions], np.float32),
        "influence_m": np.asarray([np.linalg.norm(decision.applied_position_delta_m) for decision in decisions], np.float32),
        "tag": np.asarray([observation.tag_id for observation in observations], dtype="U8"),
    }


def run_cv_tracker(observations: Iterable[PositionObservation], config: RootFilterConfig) -> tuple[dict, dict]:
    values = sorted(observations, key=lambda observation: (observation.availability_time_s,
                                                            observation.measurement_time_s,
                                                            observation.source_sequence))
    if not values:
        raise ValueError("CV tracker needs observations")
    first = values[0]
    filt = CausalDelayedRootFilter(initial_state(first.measurement_time_s, first.root_position_m), config,
                                   inertial=False)
    outputs: list[RootOutput] = []; decisions = []; used: list[PositionObservation] = []
    for observation in values:
        if observation.measurement_time_s > filt.current_state.time_s + 1e-12:
            filt.add_imu(ImuSample(
                observation.measurement_time_s, observation.availability_time_s,
                np.zeros(3), np.eye(3), observation.source_sequence,
            ))
        decision = filt.add_position(observation)
        outputs.append(filt.emit(observation.availability_time_s, decision, observation))
        decisions.append(decision); used.append(observation)
    audit = {
        "future_uwb_count": filt.future_uwb_count,
        "future_imu_count": filt.future_imu_count,
        "preavailability_output_count": filt.preavailability_output_count,
        "late_propagation_events": filt.late_imu_rejected,
        "health": filt.health_snapshot(),
    }
    return _arrays(outputs, decisions, used), audit


def run_inertial_only(samples: list[ImuSample], output_times_s: np.ndarray, *,
                      initialization_end_s: float, bias_sensor_mps2: np.ndarray,
                      config: RootFilterConfig) -> tuple[dict, dict]:
    filt = CausalDelayedRootFilter(
        initial_state(initialization_end_s, np.zeros(3), bias_sensor_mps2,
                      position_sigma_m=0.10, velocity_sigma_mps=0.10, bias_sigma_mps2=0.05),
        config, inertial=True,
    )
    ordered_samples = sorted((sample for sample in samples if sample.measurement_time_s > initialization_end_s),
                             key=lambda sample: (sample.availability_time_s, sample.measurement_time_s, sample.source_sequence))
    outputs = []
    pointer = 0
    for output_time in np.asarray(output_times_s, float):
        if output_time < initialization_end_s:
            outputs.append(None); continue
        while pointer < len(ordered_samples) and ordered_samples[pointer].availability_time_s <= output_time + 1e-12:
            filt.add_imu(ordered_samples[pointer]); pointer += 1
        outputs.append(filt.emit(float(output_time)))
    root = np.full((len(outputs), 3), np.nan, np.float32)
    velocity = np.full_like(root, np.nan); covariance = np.full_like(root, np.nan)
    mode = np.full(len(outputs), SystemMode.INITIALIZING.value, dtype="U24")
    for index, output in enumerate(outputs):
        if output is None:
            continue
        root[index] = output.root_position_m; velocity[index] = output.root_velocity_mps
        covariance[index] = np.diag(output.root_covariance_m2); mode[index] = output.active_mode.value
    return {
        "time_s": np.asarray(output_times_s, float), "root_m": root, "velocity_mps": velocity,
        "covariance_diag_m2": covariance, "mode": mode,
    }, {
        "samples_seen": pointer,
        "future_uwb_count": filt.future_uwb_count,
        "future_imu_count": filt.future_imu_count,
        "preavailability_output_count": filt.preavailability_output_count,
        "late_imu_rejected": filt.late_imu_rejected,
        "frame": "M1 gravity-aligned global gauge with arbitrary yaw; no UWB comparison is a qualified frame claim",
    }


def run_inertial_uwb_diagnostic(samples: list[ImuSample], observations: list[PositionObservation], *,
                                initialization_end_s: float, initial_position_m: np.ndarray,
                                bias_sensor_mps2: np.ndarray,
                                config: RootFilterConfig) -> tuple[dict, dict]:
    """Run full C1 only for a caller-labelled frame-assumption diagnostic."""

    filt = CausalDelayedRootFilter(
        initial_state(initialization_end_s, initial_position_m, bias_sensor_mps2), config, inertial=True)
    imu = sorted((sample for sample in samples if sample.measurement_time_s > initialization_end_s),
                 key=lambda sample: (sample.availability_time_s, sample.measurement_time_s, sample.source_sequence))
    uwb = sorted((observation for observation in observations if observation.measurement_time_s > initialization_end_s),
                 key=lambda observation: (observation.availability_time_s, observation.measurement_time_s,
                                           observation.source_sequence))
    pointer = 0; uwb_pointer = 0; outputs = []; decisions = []; used = []; pending = []
    current_availability = initialization_end_s
    while pointer < len(imu) or uwb_pointer < len(uwb):
        next_imu = imu[pointer].availability_time_s if pointer < len(imu) else np.inf
        next_uwb = uwb[uwb_pointer].availability_time_s if uwb_pointer < len(uwb) else np.inf
        current_availability = min(next_imu, next_uwb)
        if next_uwb <= next_imu:
            pending.append(uwb[uwb_pointer]); uwb_pointer += 1
        else:
            filt.add_imu(imu[pointer]); pointer += 1
        while pending and pending[0].measurement_time_s <= filt.current_state.time_s + 1e-12:
            observation = pending.pop(0)
            decision = filt.add_position(observation, processing_time_s=current_availability)
            outputs.append(filt.emit(current_availability, decision, observation))
            decisions.append(decision); used.append(observation)
    # An observation newer than the final available IMU cannot be used without
    # future inertial propagation; it remains explicitly unprocessed.
    return _arrays(outputs, decisions, used), {
        "samples_seen": pointer,
        "future_uwb_count": filt.future_uwb_count,
        "future_imu_count": filt.future_imu_count,
        "preavailability_output_count": filt.preavailability_output_count,
        "late_imu_rejected": filt.late_imu_rejected,
        "uwb_events_total": len(uwb),
        "uwb_events_processed": len(used),
        "uwb_events_waiting_for_unavailable_imu": len(pending),
        "health": filt.health_snapshot(),
        "status": "QUARANTINED_FRAME_ASSUMPTION_DIAGNOSTIC_NOT_SCIENTIFIC_CANDIDATE",
    }
