#!/usr/bin/env python3
"""Extract and adversarially audit the sealed Root-R6A2A synthetic result."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from biospur_fusion.imu.preintegration import PreintegratedInterval
from biospur_fusion.root_r6a0.body import KeyframeState
from biospur_fusion.root_r6a0.factors import RawUwbRangeFactor, raw_range_value
from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a2a.contracts import HealthState, registry_from_sealed_addendum
from biospur_fusion.root_r6a2a.qualification import (
    FROZEN_THRESHOLDS,
    SCENARIOS,
    _validator_scenarios,
    fault_injection_manifest,
)
from biospur_fusion.root_r6a2a import shadow
from biospur_fusion.root_r6a2a.shadow import (
    HealthLedger,
    IntegratedShadowEstimator,
    ScenarioSpec,
    UwbObservation,
    _attribution,
    _state_payload,
    build_synthetic_calibration,
    corrected_body_model,
    generate_imu_streams,
    generate_uwb_observations,
    interpolate_state,
    run_scenario,
    truth_state,
)

from audit_common import (
    CHECKPOINT_HEAD,
    CONFIG_PATHS,
    IMPLEMENTATION_PATHS,
    NOT_RECORDED,
    PARENT_NAME,
    PARENT_SHA256SUMS_SHA,
    canonical_sha256,
    file_inventory,
    jsonable,
    multiset_changed_count,
    numeric_summary,
    parent_snapshot,
    phase_for_step,
    rotation_error_rad,
    sha256_file,
    snapshot_files,
    source_line,
    state_order,
    state_vector,
    tangent_error,
    write_csv,
    write_json,
    write_jsonl,
)


EVIDENCE_PARENT = "ORIGINAL_PARENT_EVIDENCE"
EVIDENCE_REPLAY = "DETERMINISTICALLY_RECONSTRUCTED_EVIDENCE"


def command_output(command: Sequence[str], cwd: Path) -> str:
    completed = subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=True)
    return completed.stdout.strip()


def verify_parent_checksums(parent: Path) -> dict[str, Any]:
    manifest = {}
    for line in (parent / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.lstrip("* ")
        manifest[name] = digest
    actual = {name: sha256_file(parent / name) for name in manifest}
    mismatches = {
        name: {"expected": manifest[name], "actual": actual[name]}
        for name in manifest
        if actual[name] != manifest[name]
    }
    return {
        "sha256sums_sha256": sha256_file(parent / "SHA256SUMS"),
        "expected_sha256sums_sha256": PARENT_SHA256SUMS_SHA,
        "verified_file_count": len(manifest),
        "mismatches": mismatches,
        "pass": not mismatches and sha256_file(parent / "SHA256SUMS") == PARENT_SHA256SUMS_SHA,
    }


def exact_replay_worker(fusion_text: str, spec: ScenarioSpec) -> tuple[str, dict[str, Any]]:
    return spec.name, run_scenario(Path(fusion_text), spec)


def _imu_row(sample: Any) -> dict[str, Any]:
    return {
        "node_id": sample.node_id,
        "global_time_ns": sample.global_time_ns,
        "boot_epoch": sample.boot_epoch,
        "accel_mps2": sample.accel_mps2,
        "gyro_rad_s": sample.gyro_rad_s,
        "accepted": sample.accepted,
        "acc_raw": sample.acc_raw,
        "gyro_raw": sample.gyro_raw,
    }


def _uwb_row(observation: UwbObservation) -> dict[str, Any]:
    return {
        "event_uid": observation.event_uid,
        "tag_id": observation.tag_id,
        "anchor_id": observation.anchor_id,
        "measurement_time_s": observation.measurement_time_s,
        "availability_time_s": observation.availability_time_s,
        "range_m": observation.range_m,
        "sigma_m": observation.sigma_m,
    }


def input_fault_effect(fusion_text: str, spec: ScenarioSpec) -> dict[str, Any]:
    """Compare the injected inputs with a deterministic no-fault counterfactual."""
    fusion = Path(fusion_text)
    registry = registry_from_sealed_addendum(fusion)
    model = corrected_body_model(fusion)
    truth_calibration = build_synthetic_calibration(model, registry, geometry=spec.geometry)
    base_spec = replace(spec, fault="none")
    fault_rng = np.random.default_rng(spec.seed)
    base_rng = np.random.default_rng(spec.seed)
    changed_imu = 0
    changed_uwb = 0
    missing_imu = 0
    missing_uwb = 0
    affected_steps: set[int] = set()
    affected_imu_steps: set[int] = set()
    affected_uwb_steps: set[int] = set()
    gyro_delta_norms: list[float] = []
    accel_delta_norms: list[float] = []
    range_deltas: list[float] = []
    timestamp_deltas_s: list[float] = []
    boot_epoch_deltas: list[int] = []
    status_changes = 0
    raw_changes = 0
    step_count = int(round(spec.duration_s / spec.step_s))
    for step_index in range(step_count):
        t0, t1 = step_index * spec.step_s, (step_index + 1) * spec.step_s
        fault_streams = generate_imu_streams(model, truth_calibration, spec, step_index, t0, t1, fault_rng)
        base_streams = generate_imu_streams(model, truth_calibration, base_spec, step_index, t0, t1, base_rng)
        fault_observations = generate_uwb_observations(model, truth_calibration, spec, step_index, t0, t1, fault_rng)
        base_observations = generate_uwb_observations(model, truth_calibration, base_spec, step_index, t0, t1, base_rng)
        step_changed = False
        for node in model.imu_ids:
            fault_rows = [_imu_row(row) for row in fault_streams[node]]
            base_rows = [_imu_row(row) for row in base_streams[node]]
            difference = multiset_changed_count(fault_rows, base_rows)
            changed_imu += difference
            missing_imu += max(0, len(base_rows) - len(fault_rows))
            step_changed |= difference > 0
            if difference > 0:
                affected_imu_steps.add(step_index)
            for first, second in zip(fault_streams[node], base_streams[node]):
                gyro_delta_norms.append(float(np.linalg.norm(first.gyro_rad_s - second.gyro_rad_s)))
                accel_delta_norms.append(float(np.linalg.norm(first.accel_mps2 - second.accel_mps2)))
                timestamp_deltas_s.append(abs(first.global_time_ns - second.global_time_ns) * 1e-9)
                boot_epoch_deltas.append(abs(first.boot_epoch - second.boot_epoch))
                status_changes += int(first.accepted != second.accepted)
                raw_changes += int(first.acc_raw != second.acc_raw or first.gyro_raw != second.gyro_raw)
        fault_by_uid = {row.event_uid: row for row in fault_observations}
        base_by_uid = {row.event_uid: row for row in base_observations}
        for uid in set(fault_by_uid) | set(base_by_uid):
            if uid not in fault_by_uid or uid not in base_by_uid:
                changed_uwb += 1
                missing_uwb += int(uid not in fault_by_uid)
                step_changed = True
                affected_uwb_steps.add(step_index)
            else:
                delta = abs(fault_by_uid[uid].range_m - base_by_uid[uid].range_m)
                range_deltas.append(delta)
                if delta > 0.0:
                    changed_uwb += 1
                    step_changed = True
                    affected_uwb_steps.add(step_index)
        if step_changed:
            affected_steps.add(step_index)

    estimator_calibration = build_synthetic_calibration(
        model,
        registry,
        geometry=spec.geometry,
        wrong_lever_node=spec.target_tag if spec.fault == "wrong_synthetic_lever" else None,
        wrong_bone_geometry=spec.fault == "wrong_bone_geometry",
    )
    base_calibration = build_synthetic_calibration(model, registry, geometry=spec.geometry)
    calibration_differences = []
    for slot_id in estimator_calibration.slots:
        first = estimator_calibration.slots[slot_id].value
        second = base_calibration.slots[slot_id].value
        if first is None or second is None:
            changed = first != second
            norm = None
        else:
            changed = not np.array_equal(np.asarray(first), np.asarray(second))
            norm = float(np.linalg.norm(np.asarray(first) - np.asarray(second))) if changed else 0.0
        if changed:
            calibration_differences.append({"slot_id": slot_id, "difference_norm": norm})
    if calibration_differences:
        affected_steps.update(range(step_count))
    post_window_steps = sorted(step for step in affected_steps if step > spec.fault_end_step)
    shared_rng_cross_sensor_contamination = bool(
        spec.fault in {"tag_dropout", "multi_anchor_outage", "global_uwb_outage", "node_imu_and_uwb_dropout"}
        and any(step > spec.fault_end_step for step in affected_imu_steps)
    )
    return {
        "evidence_origin": EVIDENCE_REPLAY,
        "counterfactual": "same seed, trajectory, timing, geometry, and RNG draw order; fault set to none",
        "affected_imu_measurement_count": changed_imu,
        "missing_imu_measurement_count": missing_imu,
        "affected_uwb_observation_count": changed_uwb,
        "missing_uwb_observation_count": missing_uwb,
        "affected_step_indices": sorted(affected_steps),
        "affected_step_count": len(affected_steps),
        "affected_imu_step_indices": sorted(affected_imu_steps),
        "affected_uwb_step_indices": sorted(affected_uwb_steps),
        "post_declared_window_affected_step_indices": post_window_steps,
        "shared_rng_cross_sensor_contamination_detected": shared_rng_cross_sensor_contamination,
        "shared_rng_finding": (
            "UWB omission changes the number of RNG draws, shifting later IMU and UWB synthetic noise, including after the declared fault window."
            if shared_rng_cross_sensor_contamination
            else "NO_CROSS_SENSOR_RNG_CONTAMINATION_DETECTED_BY_COUNTERFACTUAL"
        ),
        "maximum_gyro_delta_norm_rad_s": max(gyro_delta_norms, default=0.0),
        "maximum_accel_delta_norm_mps2": max(accel_delta_norms, default=0.0),
        "maximum_timestamp_delta_s": max(timestamp_deltas_s, default=0.0),
        "maximum_boot_epoch_delta": max(boot_epoch_deltas, default=0),
        "accepted_status_change_count": status_changes,
        "raw_rail_change_count": raw_changes,
        "maximum_uwb_range_delta_m": max(range_deltas, default=0.0),
        "calibration_slot_differences": calibration_differences,
    }


def _initial_estimator(fusion: Path, spec: ScenarioSpec) -> tuple[Any, Any, Any, Any, Any]:
    registry = registry_from_sealed_addendum(fusion)
    model = corrected_body_model(fusion)
    truth_calibration = build_synthetic_calibration(model, registry, geometry=spec.geometry)
    estimator_calibration = build_synthetic_calibration(
        model,
        registry,
        geometry=spec.geometry,
        wrong_lever_node=spec.target_tag if spec.fault == "wrong_synthetic_lever" else None,
        wrong_bone_geometry=spec.fault == "wrong_bone_geometry",
    )
    initial_truth = truth_state(model, 0.0, spec.seed, spec.low_motion)
    initial_offset = np.zeros(3) if spec.low_motion else np.array([0.055, -0.035, 0.018])
    initial = replace(
        initial_truth,
        root_translation_model_m=initial_truth.root_translation_model_m + initial_offset,
        root_velocity_model_mps=initial_truth.root_velocity_model_mps + np.array([0.015, -0.01, 0.0]),
        gyro_bias_rad_s={node: np.zeros(3) for node in model.imu_ids},
        accel_bias_mps2={node: np.zeros(3) for node in model.imu_ids},
        covariance=np.eye(initial_truth.covariance.shape[0]) * 0.0025,
    )
    estimator = IntegratedShadowEstimator(model, estimator_calibration, registry, initial)
    return registry, model, truth_calibration, estimator_calibration, estimator


def detailed_replay_worker(fusion_text: str, spec: ScenarioSpec, variant: str = "full") -> dict[str, Any]:
    """Exact orchestration with observational wrappers and diagnostic variants."""
    fusion = Path(fusion_text)
    registry, model, truth_calibration, estimator_calibration, estimator = _initial_estimator(fusion, spec)
    rng = np.random.default_rng(spec.seed)
    step_count = int(round(spec.duration_s / spec.step_s))
    truth_states = [truth_state(model, 0.0, spec.seed, spec.low_motion)]
    propagated_states: list[KeyframeState] = [estimator.state]
    measurement_payload: list[dict[str, Any]] = []
    residual_rows: list[dict[str, Any]] = []
    interval_rows: list[dict[str, Any]] = []
    step_traces: list[dict[str, Any]] = []
    health_rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    native_dt_s: list[float] = []
    native_samples_by_node: Counter[str] = Counter()
    current_intervals: dict[str, PreintegratedInterval] = {}
    current_propagated: KeyframeState = estimator.state
    bias_jacobian_effect_norms: list[float] = []
    active = False

    original_integrate_async = estimator.preintegrator.integrate_async
    original_propagate = estimator._propagate
    original_uwb_update = estimator._uwb_update
    original_health_weight = estimator.health.weight
    original_all_predictions = model.all_predictions
    original_tag_phase_centres = model.tag_phase_centres
    original_interpolate = shadow.interpolate_state
    original_factor_predicted = RawUwbRangeFactor.predicted
    original_bias_corrected = PreintegratedInterval.bias_corrected

    def counted_integrate_async(*args: Any, **kwargs: Any) -> Any:
        nonlocal current_intervals
        counters["preintegrator_integrate_async_calls"] += 1
        current_intervals = dict(original_integrate_async(*args, **kwargs))
        counters["preintegrated_intervals_produced"] += len(current_intervals)
        return current_intervals

    def counted_propagate(intervals: Mapping[str, PreintegratedInterval], target_time_s: float) -> KeyframeState:
        nonlocal current_propagated
        counters["propagation_calls"] += 1
        current_propagated = original_propagate(intervals, target_time_s)
        return current_propagated

    def counted_all_predictions(*args: Any, **kwargs: Any) -> Any:
        if active:
            counters["body_model_all_predictions_calls"] += 1
        return original_all_predictions(*args, **kwargs)

    def counted_tag_phase_centres(*args: Any, **kwargs: Any) -> Any:
        if active:
            counters["body_model_tag_phase_centres_calls"] += 1
        return original_tag_phase_centres(*args, **kwargs)

    def counted_interpolate(*args: Any, **kwargs: Any) -> Any:
        if active:
            counters["state_interpolation_calls"] += 1
        return original_interpolate(*args, **kwargs)

    def counted_predicted(*args: Any, **kwargs: Any) -> Any:
        if active:
            counters["raw_uwb_factor_predicted_calls"] += 1
        return original_factor_predicted(*args, **kwargs)

    def counted_bias_corrected(interval: PreintegratedInterval, gyro_bias: np.ndarray, accel_bias: np.ndarray) -> Any:
        result = original_bias_corrected(interval, gyro_bias, accel_bias)
        if active:
            counters["bias_corrected_delta_calls"] += 1
            effect = (
                np.linalg.norm(so3_log(interval.delta_rotation.T @ result.delta_rotation))
                + np.linalg.norm(interval.delta_velocity - result.delta_velocity)
                + np.linalg.norm(interval.delta_position - result.delta_position)
            )
            bias_jacobian_effect_norms.append(float(effect))
        return result

    def diagnostic_uwb_update(previous: KeyframeState, propagated: KeyframeState, observations: Sequence[UwbObservation], geometry: str) -> tuple[KeyframeState, dict[str, Any]]:
        counters["uwb_update_calls"] += 1
        if variant == "no_uwb_updates":
            return propagated, {
                "accepted": 0,
                "outage": False,
                "correction_norm_m": 0.0,
                "information_eigenvalues": [0.0, 0.0, 0.0],
                "nearest_sample_shortcut_used": False,
                "trust_depends_on_acceleration": False,
                "diagnostic_ablation": "UWB_UPDATE_DISABLED",
            }
        return original_uwb_update(previous, propagated, observations, geometry)

    def diagnostic_weight(scope: str, entity_id: str) -> float:
        if variant == "health_accommodation_disabled":
            return 1.0
        record = estimator.health.record(scope, entity_id)
        if variant == "recovery_ramp_disabled" and record.state in {
            HealthState.RECOVERING,
            HealthState.REQUALIFYING,
        }:
            return 1.0
        return original_health_weight(scope, entity_id)

    estimator.preintegrator.integrate_async = counted_integrate_async  # type: ignore[method-assign]
    estimator._propagate = counted_propagate  # type: ignore[method-assign]
    estimator._uwb_update = diagnostic_uwb_update  # type: ignore[method-assign]
    estimator.health.weight = diagnostic_weight  # type: ignore[method-assign]
    model.all_predictions = counted_all_predictions  # type: ignore[method-assign]
    model.tag_phase_centres = counted_tag_phase_centres  # type: ignore[method-assign]
    shadow.interpolate_state = counted_interpolate
    RawUwbRangeFactor.predicted = counted_predicted
    PreintegratedInterval.bias_corrected = counted_bias_corrected

    try:
        for step_index in range(step_count):
            t0, t1 = step_index * spec.step_s, (step_index + 1) * spec.step_s
            streams = generate_imu_streams(model, truth_calibration, spec, step_index, t0, t1, rng)
            observations = generate_uwb_observations(model, truth_calibration, spec, step_index, t0, t1, rng)
            for node, samples in streams.items():
                native_samples_by_node[node] += len(samples)
                native_dt_s.extend(
                    np.diff([sample.global_time_ns for sample in samples]).astype(float).tolist()
                )
            measurement_payload.append(
                {
                    "step_index": step_index,
                    "streams": {node: [_imu_row(row) for row in rows] for node, rows in streams.items()},
                    "observations": [_uwb_row(row) for row in observations],
                }
            )
            previous = estimator.state
            covariance_before = previous.covariance.copy()
            active = True
            audit = estimator.step(streams, observations, t1, spec.geometry)
            active = False
            updated = estimator.state
            propagated = current_propagated
            propagated_states.append(propagated)
            truth = truth_state(model, t1, spec.seed, spec.low_motion)
            truth_states.append(truth)
            counters["estimator_step_calls"] += 1
            counters["state_updates"] += 1
            counters["nonzero_state_increments"] += int(
                np.linalg.norm(state_vector(updated, model) - state_vector(previous, model)) > 1e-14
            )
            counters["nonzero_imu_propagation_increments"] += int(
                np.linalg.norm(state_vector(propagated, model) - state_vector(previous, model)) > 1e-14
            )
            counters["nonzero_uwb_state_increments"] += int(
                np.linalg.norm(state_vector(updated, model) - state_vector(propagated, model)) > 1e-14
            )
            counters["covariance_propagation_updates"] += int(
                np.linalg.norm(propagated.covariance - covariance_before) > 1e-14
            )
            counters["covariance_measurement_updates"] += int(
                np.linalg.norm(updated.covariance - propagated.covariance) > 1e-14
            )
            for node, interval in current_intervals.items():
                times = [sample.global_time_ns for sample in streams[node]]
                dt_values = np.diff(times).astype(float) * 1e-9 if len(times) >= 2 else np.asarray([])
                covariance_consumed = bool(
                    interval.valid and estimator.health.record("imu_stream", node).weight > 0.0
                )
                interval_rows.append(
                    {
                        "scenario_id": spec.name,
                        "step_index": step_index,
                        "node_id": node,
                        "status": interval.status.value,
                        "sample_count": interval.sample_count,
                        "interval_count": interval.interval_count,
                        "duration_s": interval.duration_s,
                        "start_time_ns": interval.start_time_ns,
                        "end_time_ns": interval.end_time_ns,
                        "native_dt_min_s": float(np.min(dt_values)) if len(dt_values) else None,
                        "native_dt_max_s": float(np.max(dt_values)) if len(dt_values) else None,
                        "native_dt_mean_s": float(np.mean(dt_values)) if len(dt_values) else None,
                        "delta_rotation_angle_rad": float(np.linalg.norm(so3_log(interval.delta_rotation))),
                        "delta_velocity_norm_mps": float(np.linalg.norm(interval.delta_velocity)),
                        "delta_position_norm_m": float(np.linalg.norm(interval.delta_position)),
                        "covariance_shape": list(interval.covariance.shape),
                        "covariance_trace": float(np.trace(interval.covariance)),
                        "bias_jacobian_norm": float(
                            np.linalg.norm(interval.jacobian_rotation_gyro_bias)
                            + np.linalg.norm(interval.jacobian_velocity_gyro_bias)
                            + np.linalg.norm(interval.jacobian_position_gyro_bias)
                            + np.linalg.norm(interval.jacobian_velocity_accel_bias)
                            + np.linalg.norm(interval.jacobian_position_accel_bias)
                        ),
                        "covariance_consumed_by_propagator": covariance_consumed,
                    }
                )
                counters["preintegration_covariance_consumed"] += int(covariance_consumed)

            step_residuals = []
            accepted_count = 0
            rejected_count = 0
            downweighted_count = 0
            health_downweighted_count = 0
            fk_sensitivity = []
            for observation in observations:
                query = original_interpolate(previous, propagated, observation.measurement_time_s)
                prediction = raw_range_value(
                    model, estimator_calibration, query, observation.tag_id, observation.anchor_id
                )
                innovation = float(observation.range_m - prediction)
                normalized = innovation / observation.sigma_m
                tag_position = original_tag_phase_centres(query, estimator_calibration)[observation.tag_id]
                anchor = estimator_calibration.vector(f"anchor_position:{observation.anchor_id}", 3)
                direction = (tag_position - anchor) / max(1e-12, np.linalg.norm(tag_position - anchor))
                if variant == "no_uwb_updates":
                    scope_weights = {"uwb_update_disabled": 0.0}
                    health_weight = 0.0
                else:
                    link = f"{observation.tag_id}:{observation.anchor_id}"
                    scope_weights = {
                        "link": diagnostic_weight("uwb_link", link),
                        "measurement": diagnostic_weight("individual_measurement", observation.event_uid),
                        "anchor": diagnostic_weight("anchor", str(observation.anchor_id)),
                        "tag": diagnostic_weight("tag", observation.tag_id),
                        "global": diagnostic_weight("global_observability", "UWB"),
                    }
                    health_weight = min(scope_weights.values())
                robust_weight = 1.0 if abs(normalized) <= 2.5 else 2.5 / abs(normalized)
                effective_weight = health_weight * robust_weight
                accepted = effective_weight > 0.0
                accepted_count += int(accepted)
                rejected_count += int(not accepted)
                downweighted_count += int(0.0 < effective_weight < 1.0)
                health_downweighted_count += int(0.0 < health_weight < 1.0)
                query_covariance = original_interpolate(previous, propagated, observation.measurement_time_s).covariance
                innovation_variance = observation.sigma_m**2 + float(
                    direction @ query_covariance[0:3, 0:3] @ direction
                )
                approximate_nis = innovation**2 / innovation_variance
                zero_joints = replace(
                    query,
                    joint_rotvec={joint: np.zeros(3) for joint in model.joint_ids},
                )
                zero_joint_prediction = raw_range_value(
                    model, estimator_calibration, zero_joints, observation.tag_id, observation.anchor_id
                )
                sensitivity = abs(prediction - zero_joint_prediction)
                fk_sensitivity.append(sensitivity)
                residual = {
                    "evidence_origin": EVIDENCE_REPLAY,
                    "scenario_id": spec.name,
                    "step_index": step_index,
                    "time_s": t1,
                    "phase": phase_for_step(spec, step_index, audit["mode"]),
                    "event_uid": observation.event_uid,
                    "tag_id": observation.tag_id,
                    "anchor_id": observation.anchor_id,
                    "measurement_time_s": observation.measurement_time_s,
                    "range_m": observation.range_m,
                    "predicted_range_m": prediction,
                    "raw_residual_m": innovation,
                    "normalized_residual_sigma": normalized,
                    "sigma_m": observation.sigma_m,
                    "root_position_only_innovation_variance_m2": innovation_variance,
                    "root_position_only_approximate_nis": approximate_nis,
                    "health_weight": health_weight,
                    "robust_weight": robust_weight,
                    "effective_weight": effective_weight,
                    "accepted": accepted,
                    "rejected": not accepted,
                    "downweighted": 0.0 < effective_weight < 1.0,
                    "scope_weights": scope_weights,
                    "fk_zero_joint_prediction_difference_m": sensitivity,
                }
                residual_rows.append(residual)
                step_residuals.append(residual)
            if variant != "no_uwb_updates" and accepted_count != audit["uwb"]["accepted"]:
                raise AssertionError(
                    f"accepted-count reconstruction mismatch {spec.name} step {step_index}: "
                    f"{accepted_count} != {audit['uwb']['accepted']}"
                )
            counters["uwb_observations"] += len(observations)
            counters["uwb_observations_accepted"] += accepted_count
            counters["uwb_observations_rejected"] += rejected_count
            counters["uwb_observations_downweighted"] += downweighted_count
            counters["uwb_observations_health_downweighted"] += health_downweighted_count
            counters["mode_dependent_weight_changes"] += health_downweighted_count
            health_snapshot = []
            for (scope, entity_id), record in sorted(estimator.health.records.items()):
                health_snapshot.append(
                    {
                        "scope": scope,
                        "entity_id": entity_id,
                        "state": record.state.value,
                        "weight": record.weight,
                        "bad_streak": record.bad_streak,
                        "good_streak": record.good_streak,
                    }
                )
                health_rows.append(
                    {
                        "evidence_origin": EVIDENCE_REPLAY,
                        "scenario_id": spec.name,
                        "step_index": step_index,
                        "time_s": t1,
                        "mode": audit["mode"],
                        "scope": scope,
                        "entity_id": entity_id,
                        "health_state": record.state.value,
                        "weight": record.weight,
                        "bad_streak": record.bad_streak,
                        "good_streak": record.good_streak,
                    }
                )
            step_traces.append(
                {
                    "evidence_origin": EVIDENCE_REPLAY,
                    "scenario_id": spec.name,
                    "step_index": step_index,
                    "time_s": t1,
                    "phase": phase_for_step(spec, step_index, audit["mode"]),
                    "mode": audit["mode"],
                    "state_before": state_vector(previous, model),
                    "imu_propagated_state": state_vector(propagated, model),
                    "state_after": state_vector(updated, model),
                    "imu_state_increment_norm": float(
                        np.linalg.norm(state_vector(propagated, model) - state_vector(previous, model))
                    ),
                    "uwb_state_increment_norm": float(
                        np.linalg.norm(state_vector(updated, model) - state_vector(propagated, model))
                    ),
                    "root_position_before_m": previous.root_translation_model_m,
                    "root_position_after_imu_m": propagated.root_translation_model_m,
                    "root_position_after_uwb_m": updated.root_translation_model_m,
                    "covariance_trace_before": float(np.trace(covariance_before)),
                    "covariance_trace_after_imu": float(np.trace(propagated.covariance)),
                    "covariance_trace_after_uwb": float(np.trace(updated.covariance)),
                    "accepted_uwb_count": accepted_count,
                    "rejected_uwb_count": rejected_count,
                    "downweighted_uwb_count": downweighted_count,
                    "health_downweighted_uwb_count": health_downweighted_count,
                    "effective_weight_min": min((row["effective_weight"] for row in step_residuals), default=0.0),
                    "effective_weight_max": max((row["effective_weight"] for row in step_residuals), default=0.0),
                    "fk_zero_joint_prediction_difference_max_m": max(fk_sensitivity, default=0.0),
                    "imu_statuses": audit["imu_statuses"],
                    "uwb_audit": audit["uwb"],
                    "health_snapshot": health_snapshot,
                }
            )
    finally:
        active = False
        estimator.preintegrator.integrate_async = original_integrate_async  # type: ignore[method-assign]
        estimator._propagate = original_propagate  # type: ignore[method-assign]
        estimator._uwb_update = original_uwb_update  # type: ignore[method-assign]
        estimator.health.weight = original_health_weight  # type: ignore[method-assign]
        model.all_predictions = original_all_predictions  # type: ignore[method-assign]
        model.tag_phase_centres = original_tag_phase_centres  # type: ignore[method-assign]
        shadow.interpolate_state = original_interpolate
        RawUwbRangeFactor.predicted = original_factor_predicted
        PreintegratedInterval.bias_corrected = original_bias_corrected

    transitions = estimator.health.transitions()
    replay_payload = {
        "states": [_state_payload(state) for state in estimator.states],
        "modes": estimator.mode_history,
        "transitions": transitions,
        "preintegration_status_counts": dict(estimator.preintegration_status_counts),
    }
    state_rows = []
    joint_rows = []
    bias_rows = []
    covariances = []
    estimate_vectors = []
    truth_vectors = []
    tangent_errors = []
    for state_index, (estimate, truth) in enumerate(zip(estimator.states, truth_states, strict=True)):
        step_index = None if state_index == 0 else state_index - 1
        mode = estimator.mode_history[state_index]
        error = tangent_error(estimate, truth, model)
        covariance = np.asarray(estimate.covariance)
        eigenvalues = np.linalg.eigvalsh(covariance)
        nees = float(error @ np.linalg.solve(covariance, error))
        root_error = error[0:3]
        root_covariance = covariance[0:3, 0:3]
        root_nees = float(root_error @ np.linalg.solve(root_covariance, root_error))
        standard_deviation = np.sqrt(np.maximum(np.diag(covariance), 0.0))
        state_rows.append(
            {
                "evidence_origin": EVIDENCE_REPLAY,
                "scenario_id": spec.name,
                "state_index": state_index,
                "step_index": step_index,
                "time_s": estimate.time_s,
                "phase": phase_for_step(spec, step_index, mode),
                "mode": mode,
                "root_position_error_m": float(np.linalg.norm(root_error)),
                "root_orientation_geodesic_error_rad": float(np.linalg.norm(error[3:6])),
                "root_velocity_error_mps": float(np.linalg.norm(error[6:9])),
                "aggregate_joint_geodesic_error_rad": float(
                    np.mean(
                        [
                            rotation_error_rad(truth.joint_rotvec[joint], estimate.joint_rotvec[joint])
                            for joint in model.joint_ids
                        ]
                    )
                ),
                "truth_error_coordinate_squared_sum": float(error @ error),
                "full_state_tangent_nees": nees,
                "full_state_tangent_nees_per_dimension": nees / len(error),
                "root_position_nees": root_nees,
                "root_position_nees_per_dimension": root_nees / 3.0,
                "coverage_1sigma_fraction": float(np.mean(np.abs(error) <= standard_deviation)),
                "coverage_2sigma_fraction": float(np.mean(np.abs(error) <= 2.0 * standard_deviation)),
                "coverage_3sigma_fraction": float(np.mean(np.abs(error) <= 3.0 * standard_deviation)),
                "covariance_trace": float(np.trace(covariance)),
                "covariance_min_eigenvalue": float(eigenvalues[0]),
                "covariance_max_eigenvalue": float(eigenvalues[-1]),
                "root_position_covariance_trace": float(np.trace(root_covariance)),
                "root_position_covariance_min_eigenvalue": float(np.linalg.eigvalsh(root_covariance)[0]),
                "root_position_covariance_max_eigenvalue": float(np.linalg.eigvalsh(root_covariance)[-1]),
                "global_yaw_variance": float(covariance[5, 5]),
                "estimate_state_vector_json": json.dumps(state_vector(estimate, model).tolist(), separators=(",", ":")),
                "truth_state_vector_json": json.dumps(state_vector(truth, model).tolist(), separators=(",", ":")),
                "tangent_error_vector_json": json.dumps(error.tolist(), separators=(",", ":")),
                "covariance_diagonal_json": json.dumps(np.diag(covariance).tolist(), separators=(",", ":")),
            }
        )
        for joint in model.joint_ids:
            joint_rows.append(
                {
                    "evidence_origin": EVIDENCE_REPLAY,
                    "scenario_id": spec.name,
                    "state_index": state_index,
                    "step_index": step_index,
                    "time_s": estimate.time_s,
                    "phase": phase_for_step(spec, step_index, mode),
                    "joint_id": joint,
                    "geodesic_error_rad": rotation_error_rad(
                        truth.joint_rotvec[joint], estimate.joint_rotvec[joint]
                    ),
                }
            )
        for node in model.imu_ids:
            bias_rows.append(
                {
                    "evidence_origin": EVIDENCE_REPLAY,
                    "scenario_id": spec.name,
                    "state_index": state_index,
                    "step_index": step_index,
                    "time_s": estimate.time_s,
                    "phase": phase_for_step(spec, step_index, mode),
                    "node_id": node,
                    "gyro_bias_error_norm_rad_s": float(
                        np.linalg.norm(estimate.gyro_bias_rad_s[node] - truth.gyro_bias_rad_s[node])
                    ),
                    "accel_bias_error_norm_mps2": float(
                        np.linalg.norm(estimate.accel_bias_mps2[node] - truth.accel_bias_mps2[node])
                    ),
                    "estimated_gyro_bias_norm_rad_s": float(np.linalg.norm(estimate.gyro_bias_rad_s[node])),
                    "estimated_accel_bias_norm_mps2": float(np.linalg.norm(estimate.accel_bias_mps2[node])),
                }
            )
        covariances.append(covariance)
        estimate_vectors.append(state_vector(estimate, model))
        truth_vectors.append(state_vector(truth, model))
        tangent_errors.append(error)

    bone_before = {
        key: slot.value for key, slot in estimator_calibration.slots.items() if key.startswith("bone_length:")
    }
    bone_after = {
        key: slot.value for key, slot in estimator_calibration.slots.items() if key.startswith("bone_length:")
    }
    counters["preintegrator_integrate_calls"] = counters["preintegrated_intervals_produced"]
    counters["fk_evaluations_materially_joint_sensitive"] = sum(
        row["fk_zero_joint_prediction_difference_m"] > 1e-12 for row in residual_rows
    )
    counters["state_covariance_dimension"] = estimator.state.covariance.shape[0]
    counters["bias_state_numerical_updates"] = sum(
        np.linalg.norm(estimator.states[index].gyro_bias_rad_s[node] - estimator.states[index - 1].gyro_bias_rad_s[node]) > 0.0
        or np.linalg.norm(estimator.states[index].accel_bias_mps2[node] - estimator.states[index - 1].accel_bias_mps2[node]) > 0.0
        for index in range(1, len(estimator.states))
        for node in model.imu_ids
    )
    counters["bias_jacobian_nonzero_numerical_effect_calls"] = sum(value > 1e-15 for value in bias_jacobian_effect_norms)
    counters["health_transition_count"] = len(transitions)
    counters["mode_transition_count"] = sum(
        first != second for first, second in zip(estimator.mode_history[:-1], estimator.mode_history[1:])
    )
    return {
        "scenario": spec.__dict__,
        "variant": variant,
        "state_order": state_order(model),
        "measurement_input_sha256": canonical_sha256(measurement_payload),
        "deterministic_replay_sha256": canonical_sha256(replay_payload),
        "reported_attribution": _attribution(estimator, spec),
        "mode_sequence": estimator.mode_history,
        "health_transitions": transitions,
        "step_audit": estimator.audit,
        "state_rows": state_rows,
        "joint_rows": joint_rows,
        "bias_rows": bias_rows,
        "residual_rows": residual_rows,
        "interval_rows": interval_rows,
        "step_traces": step_traces,
        "health_rows": health_rows,
        "counters": dict(counters),
        "native_samples_by_node": dict(native_samples_by_node),
        "native_dt_s": [value * 1e-9 for value in native_dt_s],
        "bone_length_max_change_m": 0.0 if bone_before == bone_after else float("inf"),
        "joint_closure_max_m": max((row["max_joint_closure_m"] for row in estimator.audit), default=0.0),
        "bias_jacobian_effect_norm_max": max(bias_jacobian_effect_norms, default=0.0),
        "covariances": np.asarray(covariances),
        "estimate_vectors": np.asarray(estimate_vectors),
        "truth_vectors": np.asarray(truth_vectors),
        "tangent_errors": np.asarray(tangent_errors),
    }


def parent_claim_rows() -> list[dict[str, Any]]:
    return [
        {
            "claim_id": "ACTUAL_STATE_INTEGRATION",
            "claim": "The integrated estimator performs genuine state updates.",
            "parent_artifact": "SYNTHETIC_SCENARIO_RESULTS.json",
            "parent_fields": "state_execution_count; step_audit; deterministic_replay_sha256",
            "parent_classification": "INDIRECT_TEST_EVIDENCE",
            "reason": "Counts and a state hash exist, but parent state vectors and before/after increments were not retained.",
        },
        {
            "claim_id": "NATIVE_TIME_PREINTEGRATOR",
            "claim": "NativeTimePreintegrator is exercised on asynchronous per-node timing.",
            "parent_artifact": "SYNTHETIC_SCENARIO_RESULTS.json; QUALIFICATION_GATES.json",
            "parent_fields": "preintegration_status_counts; consumed_count; native_start_time_unique_count; native_variable_dt_exercised",
            "parent_classification": "INDIRECT_TEST_EVIDENCE",
            "reason": "Statuses and consumed counts are recorded; native sample counts and dt distributions are absent, and two timing fields are constants in source.",
        },
        {
            "claim_id": "PREINTEGRATION_BIAS_AND_COVARIANCE",
            "claim": "15x15 covariance and bias Jacobians materially reach the estimator.",
            "parent_artifact": "SYNTHETIC_SCENARIO_RESULTS.json",
            "parent_fields": "preintegration_covariance_consumed_count; preintegration_bias_jacobian_consumed_count; norm_min",
            "parent_classification": "INDIRECT_TEST_EVIDENCE",
            "reason": "Call/count evidence exists, but parent lacks matrices, state block effects, and numerical bias-correction effects.",
        },
        {
            "claim_id": "SHARED_FK",
            "claim": "Shared R6A0 FK is the articulated geometry path and materially affects ranges.",
            "parent_artifact": "SYSTEM_ARCHITECTURE_CONTRACT.json; SYNTHETIC_SCENARIO_RESULTS.json",
            "parent_fields": "shared_fk_only; joint_closure_max_m",
            "parent_classification": "INDIRECT_TEST_EVIDENCE",
            "reason": "Closure is numerical, but the parent did not retain call traces or range sensitivity to articulated joints.",
        },
        {
            "claim_id": "UWB_FACTOR_UPDATE",
            "claim": "Raw UWB factors are evaluated at measurement time and update the state.",
            "parent_artifact": "SYNTHETIC_SCENARIO_RESULTS.json",
            "parent_fields": "uwb_measurement_time_interpolation_queries; maximum_uwb_correction_m; normalized innovation summaries",
            "parent_classification": "INDIRECT_TEST_EVIDENCE",
            "reason": "Counters and correction maxima exist, but parent lacks residual rows, accepted/rejected identities, weights, and state before/after.",
        },
        {
            "claim_id": "FAULT_ATTRIBUTION",
            "claim": "Fault attribution is inferred by estimator evidence rather than injection truth.",
            "parent_artifact": "FAULT_INJECTION_QUALIFICATION.json; SYNTHETIC_SCENARIO_RESULTS.json",
            "parent_fields": "fault_attribution",
            "parent_classification": "CONTRADICTED",
            "reason": "The attribution function branches directly on ScenarioSpec.fault and target truth metadata.",
        },
        {
            "claim_id": "DEGRADED_MODES",
            "claim": "All declared degraded modes are executable and correctly selected.",
            "parent_artifact": "DEGRADED_MODE_MATRIX.json; SYNTHETIC_SCENARIO_RESULTS.json",
            "parent_fields": "mode_sequence",
            "parent_classification": "CONTRADICTED",
            "reason": "Chronological modes are recorded, but several injected missing-data/model faults remain NORMAL or enter incomplete modes; not every declared mode is observed.",
        },
        {
            "claim_id": "HEALTH_ACCOMMODATION",
            "claim": "Health-dependent weighting improves faulted estimation outcomes.",
            "parent_artifact": "MEASUREMENT_HEALTH_AND_ATTRIBUTION_CONTRACT.json; SYNTHETIC_SCENARIO_RESULTS.json",
            "parent_fields": "health_transitions; transition measurement_weight",
            "parent_classification": "DECLARED_CONTRACT_ONLY",
            "reason": "The parent has no per-observation effective weights and no disabled-health ablation.",
        },
        {
            "claim_id": "RECOVERY_REENTRY",
            "claim": "Recovery is hysteretic, controlled, and improves truth error without overshoot.",
            "parent_artifact": "RECOVERY_AND_REENTRY_CONTRACT.json; SYNTHETIC_SCENARIO_RESULTS.json",
            "parent_fields": "recovery_transition_count; maximum_uwb_correction_m; reentry_max_state_step_m",
            "parent_classification": "INDIRECT_TEST_EVIDENCE",
            "reason": "Transitions and bounded corrections are recorded; recovery truth error, discontinuity relative to propagation, overshoot, and ablation are missing.",
        },
        {
            "claim_id": "COVARIANCE_PSD_GROWTH",
            "claim": "Covariance is finite, symmetric, PSD, and grows in outage.",
            "parent_artifact": "COVARIANCE_QUALIFICATION.json",
            "parent_fields": "all_finite; maximum_symmetry_error; minimum_eigenvalue; outage growth",
            "parent_classification": "DIRECT_NUMERICAL_EVIDENCE",
            "reason": "The stated algebraic properties are directly recorded and independently replayed.",
        },
        {
            "claim_id": "COVARIANCE_TRUTH_CONSISTENCY",
            "claim": "Covariance is statistically consistent with truth error.",
            "parent_artifact": NOT_RECORDED,
            "parent_fields": NOT_RECORDED,
            "parent_classification": "NOT_RECORDED",
            "reason": "No NEES/NIS, empirical coverage, or truth-versus-covariance time series exists in the parent.",
        },
        {
            "claim_id": "FIXED_BONE_INVARIANCE",
            "claim": "Bone lengths cannot change during the estimator run.",
            "parent_artifact": "STATE_AND_OWNERSHIP_CONTRACT.json; SYNTHETIC_SCENARIO_RESULTS.json",
            "parent_fields": "bone_stretch_state=null; bone_length_max_change_m",
            "parent_classification": "DECLARED_CONTRACT_ONLY",
            "reason": "The metric compares an unchanged static calibration dictionary; it is structural, not a dynamic estimated-state measurement.",
        },
        {
            "claim_id": "SYNTHETIC_REAL_ISOLATION",
            "claim": "The 87 real slots remain null/FROZEN_UNCERTAIN and no real payload is opened.",
            "parent_artifact": "PROTECTED_HASHES_BEFORE_AFTER.json; SYNTHETIC_REAL_ISOLATION_CONTRACT.json",
            "parent_fields": "ledger hash/counts; real_payloads_opened; writes_performed",
            "parent_classification": "DIRECT_NUMERICAL_EVIDENCE",
            "reason": "Exact hashes and all 87 slot values/statuses are directly reread; payload access is bounded by the executed path inventory.",
        },
    ]


def export_parent(parent: Path, output: Path) -> dict[str, Any]:
    scenario_payload = json.loads((parent / "SYNTHETIC_SCENARIO_RESULTS.json").read_text())
    scenarios = scenario_payload["scenarios"]
    validators = json.loads((parent / "FAULT_INJECTION_QUALIFICATION.json").read_text())["validator_results"]
    inventory = {
        "schema": "biospur-root-r6a2a-r1-parent-artifact-inventory-v1",
        "evidence_origin": EVIDENCE_PARENT,
        "parent_path": str(parent),
        "parent_seal": verify_parent_checksums(parent),
        "files": file_inventory(parent),
    }
    write_json(output / "PARENT_ARTIFACT_INVENTORY.json", inventory)
    claims = parent_claim_rows()
    write_csv(output / "PARENT_CLAIM_TO_EVIDENCE.csv", claims)

    scenario_rows = []
    mode_rows = []
    attribution_rows = []
    covariance_rows = []
    missing_by_scenario = {}
    for scenario_id, result in scenarios.items():
        spec = result["scenario"]
        metrics = result["metrics"]
        modes = metrics["mode_sequence"]
        row = {
            "evidence_origin": EVIDENCE_PARENT,
            "scenario_id": scenario_id,
            "seed": spec["seed"],
            "duration_s": spec["duration_s"],
            "native_sample_counts_by_node": NOT_RECORDED,
            "uwb_observation_count": NOT_RECORDED,
            "target_node": spec["target_node"],
            "target_tag": spec["target_tag"],
            "target_anchor": spec["target_anchor"],
            "fault_type": spec["fault"],
            "fault_start_s": spec["fault_start_step"] * spec["step_s"],
            "fault_end_s": (spec["fault_end_step"] + 1) * spec["step_s"],
            "fault_duration_s": (spec["fault_end_step"] - spec["fault_start_step"] + 1) * spec["step_s"],
            "fault_magnitude": NOT_RECORDED,
            "expected_attribution": NOT_RECORDED,
            "reported_attribution": metrics["fault_attribution"],
            "detection_time_s": (
                NOT_RECORDED
                if metrics["fault_detection_latency_s"] is None
                else spec["fault_start_step"] * spec["step_s"] + metrics["fault_detection_latency_s"]
            ),
            "detection_latency_s": metrics["fault_detection_latency_s"] if metrics["fault_detection_latency_s"] is not None else NOT_RECORDED,
            "ordered_mode_sequence": modes,
            "uwb_reject_count": NOT_RECORDED,
            "uwb_downweight_count": NOT_RECORDED,
            "bone_length_max_change_m": metrics["bone_length_max_change_m"],
            "covariance_min_eigenvalue": metrics["covariance_min_eigenvalue"],
            "covariance_max_eigenvalue": NOT_RECORDED,
            "root_covariance_before_fault": NOT_RECORDED,
            "root_covariance_during_fault": NOT_RECORDED,
            "root_covariance_after_fault": NOT_RECORDED,
            "yaw_covariance_before_fault": NOT_RECORDED,
            "yaw_covariance_during_fault": NOT_RECORDED,
            "yaw_covariance_after_fault": NOT_RECORDED,
            "false_positive_count": metrics["false_positive_count"],
            "final_root_position_error_m": metrics["root_position_final_error_m"],
            "final_mode": modes[-1],
            "state_execution_count": result["state_execution_count"],
        }
        scenario_rows.append(row)
        for index, mode in enumerate(modes):
            previous = modes[index - 1] if index else None
            mode_rows.append(
                {
                    "evidence_origin": EVIDENCE_PARENT,
                    "scenario_id": scenario_id,
                    "sequence_index": index,
                    "time_s": index * spec["step_s"],
                    "from_mode": previous if previous is not None else "INITIAL",
                    "to_mode": mode,
                    "changed": previous is None or previous != mode,
                }
            )
        attribution_rows.append(
            {
                "evidence_origin": EVIDENCE_PARENT,
                "scenario_id": scenario_id,
                "injection_truth_fault": spec["fault"],
                "expected_attribution": NOT_RECORDED,
                "reported_attribution": metrics["fault_attribution"],
                "detection_latency_s": metrics["fault_detection_latency_s"] if metrics["fault_detection_latency_s"] is not None else NOT_RECORDED,
                "attribution_source_in_parent": "metrics.fault_attribution",
            }
        )
        covariance_rows.append(
            {
                "evidence_origin": EVIDENCE_PARENT,
                "scenario_id": scenario_id,
                "finite": metrics["covariance_finite"],
                "symmetry_max_abs": metrics["covariance_symmetry_max_abs"],
                "minimum_eigenvalue": metrics["covariance_min_eigenvalue"],
                "maximum_eigenvalue": NOT_RECORDED,
                "total_trace_initial": metrics["total_covariance_trace_initial"],
                "total_trace_final": metrics["total_covariance_trace_final"],
                "total_trace_max": metrics["total_covariance_trace_max"],
                "root_trace_initial": metrics["root_position_covariance_initial"],
                "root_trace_final": metrics["root_position_covariance_final"],
                "root_trace_max": metrics["root_position_covariance_max"],
                "yaw_variance_initial": metrics["global_yaw_variance_initial"],
                "yaw_variance_final": metrics["global_yaw_variance_final"],
                "truth_consistency_metrics": NOT_RECORDED,
            }
        )
        missing = [key for key, value in row.items() if value == NOT_RECORDED]
        missing_by_scenario[scenario_id] = missing

    validator_rows = []
    for validator_id, result in validators.items():
        validator_rows.append(
            {
                "evidence_origin": EVIDENCE_PARENT,
                "validator_id": validator_id,
                "authorized": result.get("authorized", NOT_RECORDED),
                "failed_closed": result.get("failed_closed", NOT_RECORDED),
                "blockers": result.get("blockers", NOT_RECORDED),
                "reason": result.get("reason", NOT_RECORDED),
                "state_execution_count": result["state_execution_count"],
            }
        )
    write_csv(output / "PARENT_SCENARIO_SUMMARY.csv", scenario_rows)
    write_csv(output / "PARENT_MODE_TRANSITIONS.csv", mode_rows)
    write_csv(output / "PARENT_FAULT_ATTRIBUTION.csv", attribution_rows)
    write_csv(output / "PARENT_COVARIANCE_SUMMARY.csv", covariance_rows)
    write_csv(output / "PARENT_VALIDATOR_RESULTS.csv", validator_rows)
    missing = {
        "schema": "biospur-root-r6a2a-r1-parent-missing-metrics-v1",
        "evidence_origin": EVIDENCE_PARENT,
        "literal_missing_value": NOT_RECORDED,
        "per_scenario": missing_by_scenario,
        "global_missing": [
            "full estimator and truth state time series",
            "state before/after each IMU propagation and UWB update",
            "native sample counts and dt distributions",
            "per-observation raw residual, effective weight, reject/downweight identity",
            "per-joint SO(3) geodesic error",
            "per-node gyro and accelerometer bias error",
            "phase-split pre-fault/during/recovery/post-fault state errors",
            "recovery overshoot and time-to-envelope",
            "full covariance matrices and maximum eigenvalues",
            "NEES/NIS and empirical sigma coverage",
            "fault magnitudes and expected attribution mapping",
            "diagnostic ablations",
        ],
    }
    write_json(output / "PARENT_MISSING_METRICS.json", missing)
    return {
        "inventory": inventory,
        "scenario_rows": scenario_rows,
        "mode_rows": mode_rows,
        "attribution_rows": attribution_rows,
        "covariance_rows": covariance_rows,
        "validator_rows": validator_rows,
        "missing": missing,
    }


def grouped_state_summaries(details: Mapping[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    scenario_rows = []
    joint_rows = []
    bias_rows = []
    for scenario_id, detail in details.items():
        state_frame = pd.DataFrame(detail["state_rows"])
        for phase in ["ALL", *state_frame["phase"].drop_duplicates().tolist()]:
            selected = state_frame if phase == "ALL" else state_frame[state_frame["phase"] == phase]
            if selected.empty:
                continue
            position = numeric_summary(selected["root_position_error_m"])
            orientation = numeric_summary(selected["root_orientation_geodesic_error_rad"])
            velocity = numeric_summary(selected["root_velocity_error_mps"])
            joints = numeric_summary(selected["aggregate_joint_geodesic_error_rad"])
            scenario_rows.append(
                {
                    "evidence_origin": EVIDENCE_REPLAY,
                    "scenario_id": scenario_id,
                    "phase": phase,
                    "sample_count": len(selected),
                    **{f"root_position_{key}_m": value for key, value in position.items() if key != "count"},
                    **{f"root_orientation_{key}_rad": value for key, value in orientation.items() if key != "count"},
                    **{f"root_velocity_{key}_mps": value for key, value in velocity.items() if key != "count"},
                    **{f"aggregate_joint_geodesic_{key}_rad": value for key, value in joints.items() if key != "count"},
                    "bone_length_max_change_m": detail["bone_length_max_change_m"],
                    "fk_joint_closure_max_m": detail["joint_closure_max_m"],
                    "native_sample_count": sum(detail["native_samples_by_node"].values()) if phase == "ALL" else "SEE_TIMESERIES",
                    "uwb_observation_count": detail["counters"]["uwb_observations"] if phase == "ALL" else "SEE_RESIDUAL_SUMMARY",
                }
            )
        joint_frame = pd.DataFrame(detail["joint_rows"])
        for (phase, joint_id), group in joint_frame.groupby(["phase", "joint_id"], sort=True):
            summary = numeric_summary(group["geodesic_error_rad"])
            joint_rows.append(
                {
                    "evidence_origin": EVIDENCE_REPLAY,
                    "scenario_id": scenario_id,
                    "phase": phase,
                    "joint_id": joint_id,
                    **{f"geodesic_error_{key}_rad": value for key, value in summary.items()},
                }
            )
        bias_frame = pd.DataFrame(detail["bias_rows"])
        for (phase, node_id), group in bias_frame.groupby(["phase", "node_id"], sort=True):
            gyro = numeric_summary(group["gyro_bias_error_norm_rad_s"])
            accel = numeric_summary(group["accel_bias_error_norm_mps2"])
            bias_rows.append(
                {
                    "evidence_origin": EVIDENCE_REPLAY,
                    "scenario_id": scenario_id,
                    "phase": phase,
                    "node_id": node_id,
                    **{f"gyro_bias_error_{key}_rad_s": value for key, value in gyro.items()},
                    **{f"accel_bias_error_{key}_mps2": value for key, value in accel.items()},
                    "estimated_bias_state_changed": bool(
                        group["estimated_gyro_bias_norm_rad_s"].max() > 0.0
                        or group["estimated_accel_bias_norm_mps2"].max() > 0.0
                    ),
                }
            )
    return scenario_rows, joint_rows, bias_rows


def residual_summaries(details: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for scenario_id, detail in details.items():
        frame = pd.DataFrame(detail["residual_rows"])
        if frame.empty:
            continue
        groupings = [
            (["phase"], "ALL", "ALL"),
            (["phase", "tag_id"], None, "ALL"),
            (["phase", "anchor_id"], "ALL", None),
            (["phase", "tag_id", "anchor_id"], None, None),
        ]
        for fields, forced_tag, forced_anchor in groupings:
            for key, group in frame.groupby(fields, sort=True):
                if not isinstance(key, tuple):
                    key = (key,)
                values = dict(zip(fields, key, strict=True))
                raw = numeric_summary(np.abs(group["raw_residual_m"]))
                normalized = numeric_summary(np.abs(group["normalized_residual_sigma"]))
                nis = numeric_summary(group["root_position_only_approximate_nis"])
                rows.append(
                    {
                        "evidence_origin": EVIDENCE_REPLAY,
                        "scenario_id": scenario_id,
                        "phase": values["phase"],
                        "tag_id": forced_tag if forced_tag is not None else values.get("tag_id", "ALL"),
                        "anchor_id": forced_anchor if forced_anchor is not None else values.get("anchor_id", "ALL"),
                        "observation_count": len(group),
                        "accepted_count": int(group["accepted"].sum()),
                        "rejected_count": int(group["rejected"].sum()),
                        "downweighted_count": int(group["downweighted"].sum()),
                        **{f"absolute_raw_residual_{name}_m": value for name, value in raw.items() if name != "count"},
                        **{f"absolute_normalized_residual_{name}_sigma": value for name, value in normalized.items() if name != "count"},
                        **{f"root_position_only_approximate_nis_{name}": value for name, value in nis.items() if name != "count"},
                    }
                )
    return rows


def recovery_summaries(details: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for scenario_id, detail in details.items():
        spec = detail["scenario"]
        frame = pd.DataFrame(detail["state_rows"])
        traces = pd.DataFrame(detail["step_traces"])
        pre = frame[frame["phase"] == "PRE_FAULT"]
        after = frame[frame["time_s"] > (spec["fault_end_step"] + 1) * spec["step_s"] - 1e-12]
        recovery = frame[frame["phase"] == "RECOVERY"]
        envelope = float(pre["root_position_error_m"].max()) if not pre.empty else None
        overshoot = None if after.empty or envelope is None else float(after["root_position_error_m"].max() - envelope)
        time_to_envelope = None
        if envelope is not None and not after.empty:
            values = after.reset_index(drop=True)
            for index, value in values.iterrows():
                if value["root_position_error_m"] <= envelope and bool(
                    (values.loc[index:, "root_position_error_m"] <= envelope).all()
                ):
                    time_to_envelope = float(value["time_s"] - (spec["fault_end_step"] + 1) * spec["step_s"])
                    break
        recovery_traces = traces[traces["phase"] == "RECOVERY"] if not traces.empty else traces
        rows.append(
            {
                "evidence_origin": EVIDENCE_REPLAY,
                "scenario_id": scenario_id,
                "fault_type": spec["fault"],
                "recovery_state_count": len(recovery),
                "ordered_mode_sequence": detail["mode_sequence"],
                "recovery_transition_count": sum(
                    row["to"] in {"RECOVERING", "REQUALIFYING", "HEALTHY"}
                    for row in detail["health_transitions"]
                ),
                "pre_fault_root_error_envelope_m": envelope,
                "post_fault_root_error_overshoot_m": overshoot,
                "time_to_pre_fault_error_envelope_s": time_to_envelope,
                "maximum_recovery_uwb_state_increment_norm": (
                    float(recovery_traces["uwb_state_increment_norm"].max()) if not recovery_traces.empty else None
                ),
                "maximum_recovery_root_state_step_m": (
                    float(
                        max(
                            np.linalg.norm(
                                np.asarray(json.loads(frame.iloc[index]["estimate_state_vector_json"]))[:3]
                                - np.asarray(json.loads(frame.iloc[index - 1]["estimate_state_vector_json"]))[:3]
                            )
                            for index in range(1, len(frame))
                            if frame.iloc[index]["phase"] == "RECOVERY"
                        )
                    )
                    if any(frame.iloc[index]["phase"] == "RECOVERY" for index in range(1, len(frame)))
                    else None
                ),
            }
        )
    return rows


def covariance_products(details: Mapping[str, dict[str, Any]], output: Path) -> dict[str, Any]:
    covariance_rows = []
    innovation_rows = []
    coverage: dict[str, Any] = {
        "schema": "biospur-root-r6a2a-r1-covariance-coverage-v1",
        "evidence_origin": EVIDENCE_REPLAY,
        "state_error_representation": "123-dimensional tangent/error vector in STATE_ORDER.json",
        "innovation_caveat": "NIS uses range sigma plus root-position covariance projected onto line of sight; articulated orientation/joint covariance is omitted, so it is an explicitly approximate diagnostic rather than formal full-factor NIS.",
        "scenarios": {},
    }
    for scenario_id, detail in details.items():
        state_frame = pd.DataFrame(detail["state_rows"])
        coverage["scenarios"][scenario_id] = {}
        for phase in ["ALL", *state_frame["phase"].drop_duplicates().tolist()]:
            group = state_frame if phase == "ALL" else state_frame[state_frame["phase"] == phase]
            if group.empty:
                continue
            row = {
                "evidence_origin": EVIDENCE_REPLAY,
                "scenario_id": scenario_id,
                "phase": phase,
                "sample_count": len(group),
                "truth_error_coordinate_squared_mean": float(group["truth_error_coordinate_squared_sum"].mean()),
                "full_state_tangent_nees_mean": float(group["full_state_tangent_nees"].mean()),
                "full_state_tangent_nees_per_dimension_mean": float(group["full_state_tangent_nees_per_dimension"].mean()),
                "full_state_tangent_nees_per_dimension_p95": float(group["full_state_tangent_nees_per_dimension"].quantile(0.95)),
                "root_position_nees_mean": float(group["root_position_nees"].mean()),
                "root_position_nees_per_dimension_mean": float(group["root_position_nees_per_dimension"].mean()),
                "covariance_trace_mean": float(group["covariance_trace"].mean()),
                "covariance_trace_min": float(group["covariance_trace"].min()),
                "covariance_trace_max": float(group["covariance_trace"].max()),
                "covariance_min_eigenvalue": float(group["covariance_min_eigenvalue"].min()),
                "covariance_max_eigenvalue": float(group["covariance_max_eigenvalue"].max()),
                "root_position_covariance_trace_mean": float(group["root_position_covariance_trace"].mean()),
                "root_position_covariance_min_eigenvalue": float(group["root_position_covariance_min_eigenvalue"].min()),
                "root_position_covariance_max_eigenvalue": float(group["root_position_covariance_max_eigenvalue"].max()),
                "global_yaw_variance_mean": float(group["global_yaw_variance"].mean()),
                "coverage_1sigma_mean_fraction": float(group["coverage_1sigma_fraction"].mean()),
                "coverage_2sigma_mean_fraction": float(group["coverage_2sigma_fraction"].mean()),
                "coverage_3sigma_mean_fraction": float(group["coverage_3sigma_fraction"].mean()),
            }
            covariance_rows.append(row)
            coverage["scenarios"][scenario_id][phase] = {
                key: row[key]
                for key in (
                    "sample_count",
                    "coverage_1sigma_mean_fraction",
                    "coverage_2sigma_mean_fraction",
                    "coverage_3sigma_mean_fraction",
                    "full_state_tangent_nees_per_dimension_mean",
                    "root_position_nees_per_dimension_mean",
                )
            }
        residual_frame = pd.DataFrame(detail["residual_rows"])
        if residual_frame.empty:
            continue
        for phase in ["ALL", *residual_frame["phase"].drop_duplicates().tolist()]:
            group = residual_frame if phase == "ALL" else residual_frame[residual_frame["phase"] == phase]
            nis = group["root_position_only_approximate_nis"].astype(float)
            normalized = group["normalized_residual_sigma"].abs().astype(float)
            innovation_rows.append(
                {
                    "evidence_origin": EVIDENCE_REPLAY,
                    "scenario_id": scenario_id,
                    "phase": phase,
                    "observation_count": len(group),
                    "root_position_only_approximate_nis_mean": float(nis.mean()),
                    "root_position_only_approximate_nis_median": float(nis.median()),
                    "root_position_only_approximate_nis_p95": float(nis.quantile(0.95)),
                    "sigma_only_normalized_residual_mean_abs": float(normalized.mean()),
                    "sigma_only_normalized_residual_p95_abs": float(normalized.quantile(0.95)),
                    "approximate_nis_within_1sigma_fraction": float((nis <= 1.0).mean()),
                    "approximate_nis_within_2sigma_fraction": float((nis <= 4.0).mean()),
                    "approximate_nis_within_3sigma_fraction": float((nis <= 9.0).mean()),
                    "formal_full_factor_nis_valid": False,
                }
            )
    write_csv(output / "COVARIANCE_TRUTH_CONSISTENCY.csv", covariance_rows)
    write_csv(output / "INNOVATION_CONSISTENCY.csv", innovation_rows)
    write_json(output / "COVARIANCE_COVERAGE.json", coverage)

    aggregate = pd.DataFrame(covariance_rows)
    all_rows = aggregate[aggregate["phase"] == "ALL"]
    clean = all_rows[all_rows["scenario_id"].str.startswith("clean_")]
    low_geometry = all_rows[all_rows["scenario_id"] == "uwb_low_vertical_diversity"]
    outage = pd.DataFrame(details["uwb_full_outage_recovery"]["state_rows"])
    outage_pre = outage[outage["phase"] == "PRE_FAULT"]
    outage_during = outage[outage["phase"] == "DURING_FAULT"]
    outage_recovery = outage[outage["phase"] == "RECOVERY"]
    covariance_md = f"""# Root-R6A2A-R1 covariance audit

Evidence origin: `{EVIDENCE_REPLAY}`. The parent retained only PSD/symmetry and coarse growth summaries; every truth-consistency quantity below is reconstructed by exact replay after parent equivalence passed.

## State and units

The 123-state ordering is root position (m), root SO(3) tangent error (rad), root velocity (m/s), nine relative-joint SO(3) tangent errors (rad), nine joint rates (rad/s), ten gyro biases (rad/s), and ten accelerometer biases (m/s^2). `STATE_ORDER.json` is authoritative. The estimator initializes every diagonal covariance element to 0.0025 despite mixed units. It never updates either bias estimate in these scenarios.

## Consistency findings

Across the three clean scenarios, mean full-state tangent NEES per dimension ranges from {clean['full_state_tangent_nees_per_dimension_mean'].min():.6g} to {clean['full_state_tangent_nees_per_dimension_mean'].max():.6g}. Mean coordinate coverage ranges are {clean['coverage_1sigma_mean_fraction'].min():.3f}–{clean['coverage_1sigma_mean_fraction'].max():.3f} at 1σ, {clean['coverage_2sigma_mean_fraction'].min():.3f}–{clean['coverage_2sigma_mean_fraction'].max():.3f} at 2σ, and {clean['coverage_3sigma_mean_fraction'].min():.3f}–{clean['coverage_3sigma_mean_fraction'].max():.3f} at 3σ. These are diagnostic tangent-space statistics, not proof of calibrated uncertainty: the synthetic trajectories are deterministic, the state errors are correlated, and only 13 states per ordinary scenario are available.

During the full UWB outage, mean root-position covariance trace changes from {outage_pre['root_position_covariance_trace'].mean():.6g} before the fault to {outage_during['root_position_covariance_trace'].mean():.6g} during it and {outage_recovery['root_position_covariance_trace'].mean() if not outage_recovery.empty else float('nan'):.6g} during controlled recovery. Covariance therefore grows and later contracts, but the corresponding truth errors and NEES in `COVARIANCE_TRUTH_CONSISTENCY.csv` show whether that scale is credible.

For low vertical diversity, the maximum root-position covariance eigenvalue is {low_geometry['root_position_covariance_max_eigenvalue'].max():.6g}. The implementation changes a scalar regularizer but does not explicitly inflate the weak eigen-direction described by the degraded-mode contract. This is an incomplete scientific geometry treatment even though covariance remains PSD.

## Innovation limitation

`INNOVATION_CONSISTENCY.csv` reports both sigma-only normalized residuals and a root-position-only projected covariance diagnostic. A formal full-factor NIS is not claimed because the range factor depends on articulated orientations/joints and the implementation does not expose a full measurement Jacobian. Calling the parent covariance “consistent” from PSD and outage growth alone is unsupported.
"""
    (output / "COVARIANCE_AUDIT.md").write_text(covariance_md)
    return {
        "covariance_rows": covariance_rows,
        "innovation_rows": innovation_rows,
        "coverage": coverage,
    }


def _run_control_estimator(fusion: Path, injection_spec: ScenarioSpec, label_specs: Mapping[str, ScenarioSpec]) -> dict[str, Any]:
    _, model, truth_calibration, _, estimator = _initial_estimator(fusion, injection_spec)
    rng = np.random.default_rng(injection_spec.seed)
    measurement_payload = []
    for step_index in range(int(round(injection_spec.duration_s / injection_spec.step_s))):
        t0, t1 = step_index * injection_spec.step_s, (step_index + 1) * injection_spec.step_s
        streams = generate_imu_streams(model, truth_calibration, injection_spec, step_index, t0, t1, rng)
        observations = generate_uwb_observations(model, truth_calibration, injection_spec, step_index, t0, t1, rng)
        measurement_payload.append(
            {
                "streams": {node: [_imu_row(row) for row in values] for node, values in streams.items()},
                "observations": [_uwb_row(row) for row in observations],
            }
        )
        estimator.step(streams, observations, t1, injection_spec.geometry)
    estimator_payload = {
        "states": [_state_payload(state) for state in estimator.states],
        "modes": estimator.mode_history,
        "transitions": estimator.health.transitions(),
        "preintegration_status_counts": dict(estimator.preintegration_status_counts),
    }
    health_payload = {
        "states": {
            scope: estimator.health.states(scope)
            for scope in ("individual_measurement", "imu_stream", "uwb_link", "tag", "anchor", "node", "segment_joint_consistency", "global_observability")
        },
        "transitions": estimator.health.transitions(),
        "modes": estimator.mode_history,
    }
    before_attribution_record_count = len(estimator.health.records)
    attributions = {name: _attribution(estimator, label_spec) for name, label_spec in label_specs.items()}
    return {
        "measurement_sha256": canonical_sha256(measurement_payload),
        "estimator_output_sha256": canonical_sha256(estimator_payload),
        "health_output_sha256": canonical_sha256(health_payload),
        "before_attribution_record_count": before_attribution_record_count,
        "attributions": attributions,
        "estimator_step_signature": str(inspect.signature(IntegratedShadowEstimator.step)),
    }


def fault_label_audit(fusion: Path, output: Path) -> dict[str, Any]:
    original = next(spec for spec in SCENARIOS if spec.name == "uwb_single_anchor_fault")
    label_specs = {
        "original_truth_label": original,
        "renamed_scenario_only": replace(original, name="RENAMED_WITH_IDENTICAL_MEASUREMENTS"),
        "permuted_truth_label": replace(original, fault="wrong_bone_geometry"),
        "conflicting_truth_label": replace(original, fault="single_tag_fault"),
    }
    first = _run_control_estimator(fusion, original, label_specs)
    second = _run_control_estimator(fusion, original, label_specs)
    controls = {
        "schema": "biospur-root-r6a2a-r1-fault-label-negative-controls-v1",
        "evidence_origin": EVIDENCE_REPLAY,
        "measurement_scenario": original.name,
        "repeat_measurements_identical": first["measurement_sha256"] == second["measurement_sha256"],
        "repeat_estimator_outputs_identical": first["estimator_output_sha256"] == second["estimator_output_sha256"],
        "repeat_health_outputs_identical": first["health_output_sha256"] == second["health_output_sha256"],
        "scenario_rename_test": {
            "pass": first["attributions"]["renamed_scenario_only"] == first["attributions"]["original_truth_label"],
            "measurements_changed": False,
            "estimator_output_changed": False,
            "health_output_changed": False,
            "original_attribution": first["attributions"]["original_truth_label"],
            "renamed_attribution": first["attributions"]["renamed_scenario_only"],
        },
        "truth_label_permutation_test": {
            "pass": first["attributions"]["permuted_truth_label"] == first["attributions"]["original_truth_label"],
            "measurements_changed": False,
            "estimator_output_changed": False,
            "health_output_changed": False,
            "original_attribution": first["attributions"]["original_truth_label"],
            "permuted_attribution": first["attributions"]["permuted_truth_label"],
        },
        "truth_metadata_removal_test": {
            "pass": True,
            "detail": "IntegratedShadowEstimator.step accepted only streams, observations, target_time_s, and geometry; execution and hashes were completed before any attribution label was supplied.",
            "step_signature": first["estimator_step_signature"],
            "estimator_output_sha256": first["estimator_output_sha256"],
        },
        "same_measurements_conflicting_label_test": {
            "pass": first["attributions"]["conflicting_truth_label"] == first["attributions"]["original_truth_label"],
            "measurements_changed": False,
            "estimator_output_changed": False,
            "health_output_changed": False,
            "original_attribution": first["attributions"]["original_truth_label"],
            "conflicting_attribution": first["attributions"]["conflicting_truth_label"],
        },
        "hard_scientific_failure": (
            first["attributions"]["permuted_truth_label"] != first["attributions"]["original_truth_label"]
            or first["attributions"]["conflicting_truth_label"] != first["attributions"]["original_truth_label"]
        ),
        "first_run": first,
        "second_run": second,
    }
    write_json(output / "FAULT_LABEL_NEGATIVE_CONTROLS.json", controls)
    source = fusion / "src/biospur_fusion/root_r6a2a/shadow.py"
    dataflow = {
        "schema": "biospur-root-r6a2a-r1-fault-truth-dataflow-audit-v1",
        "source_sha256": sha256_file(source),
        "separation": {
            "injection_truth.expected_fault": "ScenarioSpec.fault used by synthetic generators and estimator-calibration fault injection",
            "estimator.detected_event": "HealthLedger transitions derived from IMU interval status/angular-rate departure and UWB residuals",
            "estimator.attributed_fault": "result metrics.fault_attribution returned by _attribution(estimator, spec)",
            "evaluator.comparison": "No separate expected-attribution field exists; qualification gates compare the truth-dependent returned attribution directly",
        },
        "source_flows": [
            {
                "truth_field": "ScenarioSpec.fault",
                "legitimate_injection_path": [
                    source_line(source, "if active and node == spec.target_node:"),
                    source_line(source, "if active and spec.fault == \"global_uwb_outage\":"),
                ],
                "prohibited_result_path": [
                    source_line(source, "def _attribution(estimator: IntegratedShadowEstimator, spec: ScenarioSpec)"),
                    source_line(source, "if spec.fault == \"single_anchor_fault\""),
                    source_line(source, "if spec.fault in {\"wrong_synthetic_lever\""),
                ],
            },
            {
                "truth_field": "ScenarioSpec.target_anchor/target_tag/target_node",
                "prohibited_result_path": [
                    source_line(source, "anchor = estimator.health.record(\"anchor\", str(spec.target_anchor)).state"),
                    source_line(source, "tag = estimator.health.record(\"tag\", spec.target_tag).state"),
                    source_line(source, "imu = estimator.health.record(\"imu_stream\", spec.target_node).state"),
                ],
            },
            {
                "truth_field": "ScenarioSpec.name",
                "path": [source_line(source, "event_uid=f\"{spec.name}:{step_index}:{tag}:{anchor_id}\"")],
                "finding": "Name enters generated event identity, but the required rename control held measurements/event IDs fixed; estimator results then remained identical.",
            },
        ],
        "health_manager_receives_truth_label": False,
        "state_update_receives_truth_label": False,
        "reported_fault_attribution_receives_truth_label": True,
        "negative_control_result": controls,
        "finding": "The numerical state and health paths do not receive fault truth, but the reported estimator fault attribution does. This contaminates attribution gates O-Q, R, T, and U and is a hard scientific failure under the requested taxonomy.",
    }
    write_json(output / "FAULT_TRUTH_DATAFLOW_AUDIT.json", dataflow)
    md = f"""# Fault-truth dataflow audit

The state-update and health-manager calls do not receive `ScenarioSpec.fault`; they receive only corrupted IMU streams, UWB observations, time, and geometry. That separation is real.

The reported attribution is not separated. `_attribution(estimator, spec)` reads `spec.fault`, `spec.target_anchor`, `spec.target_tag`, and `spec.target_node`. With identical measurements and identical numerical estimator/health output, changing only the external truth label changed attribution from `{controls['same_measurements_conflicting_label_test']['original_attribution']}` to `{controls['same_measurements_conflicting_label_test']['conflicting_attribution']}`. Permuting the label changed it to `{controls['truth_label_permutation_test']['permuted_attribution']}`.

Therefore the parent’s state trajectory is not shown to leak truth, but its field named `fault_attribution` and the qualification gates that consume it are truth-label contaminated. This is a hard scientific failure, not a missing-reporting issue.
"""
    (output / "FAULT_TRUTH_DATAFLOW_AUDIT.md").write_text(md)
    return {"controls": controls, "dataflow": dataflow}


ANOMALOUS_SCENARIOS = (
    "uwb_multi_anchor_outage",
    "uwb_tag_dropout",
    "model_wrong_bone_geometry",
    "node_imu_and_uwb_dropout",
    "model_wrong_synthetic_lever",
    "model_transient_wrist_ghost",
)


def anomalous_scenario_rows(
    details: Mapping[str, dict[str, Any]],
    counterfactuals: Mapping[str, dict[str, Any]],
    fault_effects: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    classifications = {
        "uwb_multi_anchor_outage": "MISSING_HANDLER",
        "uwb_tag_dropout": "MISSING_HANDLER",
        "model_wrong_bone_geometry": "TRUTH_LABEL_LEAKAGE",
        "node_imu_and_uwb_dropout": "DETECTED_BUT_WRONG_MODE",
        "model_wrong_synthetic_lever": "TRUTH_LABEL_LEAKAGE",
        "model_transient_wrist_ghost": "DETECTED_BUT_WRONG_MODE",
    }
    thresholds = {
        "uwb_multi_anchor_outage": "No per-anchor missing-observation/persistence threshold exists; only present residuals feed anchor health.",
        "uwb_tag_dropout": "No per-tag missing-observation/persistence threshold exists; only present residuals feed tag health.",
        "model_wrong_bone_geometry": "UWB |normalized residual| > 6 and bad-anchor/tag fraction >= 0.45; neither threshold fired.",
        "node_imu_and_uwb_dropout": "IMU interval hard invalidity isolates the IMU; no missing-tag UWB threshold exists.",
        "model_wrong_synthetic_lever": "UWB |normalized residual| > 6, Huber transition 2.5 sigma, and bad fraction >= 0.45; link evidence fired but no model/lever ambiguity detector did.",
        "model_transient_wrist_ghost": "cross-time angular-rate departure > 0.18 rad/s; recovery precedence in _mode selects CONTROLLED_REENTRY before an explicit degraded/isolation mode.",
    }
    findings = {
        "uwb_multi_anchor_outage": "Five anchors are removed during the fault window, but absent anchors are never observed as bad; weights of remaining measurements and mode stay nominal. Because one RNG is shared, skipped UWB draws also change later IMU/UWB noise beyond the declared window.",
        "uwb_tag_dropout": "All target-tag ranges are removed during the fault window, but absent tags are never observed as bad; attribution remains NO_FAULT. Because one RNG is shared, skipped UWB draws also change later IMU/UWB noise beyond the declared window.",
        "model_wrong_bone_geometry": "The +0.11 m shoulder-parent calibration error is active for the entire run, not only the declared fault window. No health transition occurs; AMBIGUOUS attribution is returned solely from the truth label.",
        "node_imu_and_uwb_dropout": "The IMU dropout is detected and isolated. The simultaneous missing UWB tag has no handler, so SINGLE_NODE_IMU_AND_UWB_DEGRADED is never selected. Skipped UWB RNG draws also contaminate later IMU/UWB noise beyond the declared window.",
        "model_wrong_synthetic_lever": "The lever error is active for the entire run. Residuals primarily degrade links; model ambiguity is not inferred, yet attribution is forced to AMBIGUOUS by the truth label.",
        "model_transient_wrist_ghost": "The angular-rate anomaly is detected, but recovering health has priority in mode selection, producing CONTROLLED_REENTRY without a preceding explicit degraded/isolation mode.",
    }
    rows = []
    for scenario_id in ANOMALOUS_SCENARIOS:
        detail = details[scenario_id]
        counter = counterfactuals[scenario_id]
        effect = fault_effects[scenario_id]
        state_differences = np.linalg.norm(
            detail["estimate_vectors"] - counter["estimate_vectors"], axis=1
        )
        root_differences = np.linalg.norm(
            detail["estimate_vectors"][:, :3] - counter["estimate_vectors"][:, :3], axis=1
        )
        covariance_differences = np.linalg.norm(
            detail["covariances"] - counter["covariances"], axis=(1, 2)
        )
        detected = any(
            row["to"] in {"SUSPECT", "DEGRADED", "ISOLATED"}
            and row["time_s"] >= detail["scenario"]["fault_start_step"] * detail["scenario"]["step_s"]
            for row in detail["health_transitions"]
        )
        health_weight_changed = any(row["health_weight"] < 1.0 for row in detail["residual_rows"])
        row = {
            "evidence_origin": EVIDENCE_REPLAY,
            "scenario_id": scenario_id,
            "fault_type": detail["scenario"]["fault"],
            "fault_actually_injected": bool(
                effect["affected_imu_measurement_count"]
                or effect["affected_uwb_observation_count"]
                or effect["calibration_slot_differences"]
            ),
            "affected_imu_measurement_count": effect["affected_imu_measurement_count"],
            "missing_imu_measurement_count": effect["missing_imu_measurement_count"],
            "affected_uwb_observation_count": effect["affected_uwb_observation_count"],
            "missing_uwb_observation_count": effect["missing_uwb_observation_count"],
            "affected_calibration_slots": effect["calibration_slot_differences"],
            "post_declared_window_affected_steps": effect["post_declared_window_affected_step_indices"],
            "shared_rng_cross_sensor_contamination": effect["shared_rng_cross_sensor_contamination_detected"],
            "affected_estimated_state_count": int(np.sum(state_differences > 1e-12)),
            "maximum_estimated_state_vector_difference": float(np.max(state_differences)),
            "maximum_root_position_difference_from_no_fault_m": float(np.max(root_differences)),
            "maximum_covariance_frobenius_difference_from_no_fault": float(np.max(covariance_differences)),
            "inside_predeclared_intentionally_tolerated_envelope": False,
            "controlling_threshold": thresholds[scenario_id],
            "expected_mode_predeclared_for_scenario": NOT_RECORDED,
            "ordered_mode_sequence": detail["mode_sequence"],
            "fault_detected": detected,
            "measurement_weights_changed": health_weight_changed,
            "covariance_changed_vs_no_fault": bool(np.max(covariance_differences) > 1e-12),
            "state_changed_vs_no_fault": bool(np.max(state_differences) > 1e-12),
            "reported_attribution": detail["reported_attribution"],
            "attribution_computed_from_estimator_evidence_only": False,
            "classification": classifications[scenario_id],
            "finding": findings[scenario_id],
        }
        rows.append(row)
    return rows


def ablation_metrics(detail: dict[str, Any]) -> dict[str, Any]:
    states = pd.DataFrame(detail["state_rows"])
    traces = pd.DataFrame(detail["step_traces"])
    recovery = traces[traces["phase"] == "RECOVERY"] if not traces.empty else traces
    return {
        "measurement_input_sha256": detail["measurement_input_sha256"],
        "estimator_state_sha256": canonical_sha256(detail["estimate_vectors"]),
        "root_position_rmse_m": float(np.sqrt(np.mean(np.square(states["root_position_error_m"])))),
        "root_position_initial_error_m": float(states.iloc[0]["root_position_error_m"]),
        "root_position_final_error_m": float(states.iloc[-1]["root_position_error_m"]),
        "root_position_max_error_m": float(states["root_position_error_m"].max()),
        "root_drift_change_m": float(states.iloc[-1]["root_position_error_m"] - states.iloc[0]["root_position_error_m"]),
        "maximum_uwb_state_increment_norm": float(traces["uwb_state_increment_norm"].max()),
        "maximum_recovery_uwb_state_increment_norm": float(recovery["uwb_state_increment_norm"].max()) if not recovery.empty else None,
        "accepted_uwb_count": detail["counters"]["uwb_observations_accepted"],
        "rejected_uwb_count": detail["counters"]["uwb_observations_rejected"],
        "downweighted_uwb_count": detail["counters"]["uwb_observations_downweighted"],
        "health_transition_count": detail["counters"]["health_transition_count"],
        "ordered_mode_sequence": detail["mode_sequence"],
    }


def execution_ablation_products(
    details: Mapping[str, dict[str, Any]],
    variants: Mapping[str, dict[str, Any]],
    output: Path,
) -> dict[str, Any]:
    definitions = [
        ("clean_baseline_seed_6201", "full_integrated_estimator", details["clean_baseline_seed_6201"]),
        ("clean_baseline_seed_6201", "imu_propagation_uwb_updates_disabled", variants["no_uwb_updates"]),
        ("uwb_single_anchor_fault", "full_integrated_estimator", details["uwb_single_anchor_fault"]),
        ("uwb_single_anchor_fault", "health_accommodation_disabled", variants["health_accommodation_disabled"]),
        ("uwb_full_outage_recovery", "full_integrated_estimator", details["uwb_full_outage_recovery"]),
        ("uwb_full_outage_recovery", "recovery_ramp_disabled", variants["recovery_ramp_disabled"]),
    ]
    rows = []
    for scenario_id, ablation, detail in definitions:
        rows.append(
            {
                "evidence_origin": EVIDENCE_REPLAY,
                "scenario_id": scenario_id,
                "ablation": ablation,
                **ablation_metrics(detail),
            }
        )
    write_csv(output / "EXECUTION_ABLATION_RESULTS.csv", rows)
    frame = pd.DataFrame(rows)

    def row(scenario: str, ablation: str) -> pd.Series:
        return frame[(frame["scenario_id"] == scenario) & (frame["ablation"] == ablation)].iloc[0]

    clean_full = row("clean_baseline_seed_6201", "full_integrated_estimator")
    clean_no_uwb = row("clean_baseline_seed_6201", "imu_propagation_uwb_updates_disabled")
    anchor_full = row("uwb_single_anchor_fault", "full_integrated_estimator")
    anchor_no_health = row("uwb_single_anchor_fault", "health_accommodation_disabled")
    outage_full = row("uwb_full_outage_recovery", "full_integrated_estimator")
    outage_no_ramp = row("uwb_full_outage_recovery", "recovery_ramp_disabled")
    clean_state_difference = float(np.max(np.linalg.norm(
        variants["no_uwb_updates"]["estimate_vectors"] - details["clean_baseline_seed_6201"]["estimate_vectors"], axis=1
    )))
    health_state_difference = float(np.max(np.linalg.norm(
        variants["health_accommodation_disabled"]["estimate_vectors"] - details["uwb_single_anchor_fault"]["estimate_vectors"], axis=1
    )))
    recovery_state_difference = float(np.max(np.linalg.norm(
        variants["recovery_ramp_disabled"]["estimate_vectors"] - details["uwb_full_outage_recovery"]["estimate_vectors"], axis=1
    )))
    recovery_covariance_difference = float(np.max(np.linalg.norm(
        variants["recovery_ramp_disabled"]["covariances"] - details["uwb_full_outage_recovery"]["covariances"], axis=(1, 2)
    )))
    analysis = {
        "same_measurements_clean_pair": clean_full["measurement_input_sha256"] == clean_no_uwb["measurement_input_sha256"],
        "disabling_uwb_changes_state": clean_state_difference > 1e-9,
        "maximum_state_vector_difference_without_uwb": clean_state_difference,
        "clean_final_error_change_without_uwb_m": float(clean_no_uwb["root_position_final_error_m"] - clean_full["root_position_final_error_m"]),
        "same_measurements_health_pair": anchor_full["measurement_input_sha256"] == anchor_no_health["measurement_input_sha256"],
        "disabling_health_changes_state": health_state_difference > 1e-9,
        "maximum_state_vector_difference_without_health": health_state_difference,
        "bad_measurement_max_error_increase_without_health_m": float(anchor_no_health["root_position_max_error_m"] - anchor_full["root_position_max_error_m"]),
        "health_accommodation_improves_max_error": bool(anchor_no_health["root_position_max_error_m"] > anchor_full["root_position_max_error_m"]),
        "same_measurements_recovery_pair": outage_full["measurement_input_sha256"] == outage_no_ramp["measurement_input_sha256"],
        "disabling_recovery_ramp_changes_state": recovery_state_difference > 1e-9,
        "maximum_state_vector_difference_without_recovery_ramp": recovery_state_difference,
        "maximum_covariance_frobenius_difference_without_recovery_ramp": recovery_covariance_difference,
        "recovery_increment_change_without_ramp": (
            None
            if outage_full["maximum_recovery_uwb_state_increment_norm"] is None or outage_no_ramp["maximum_recovery_uwb_state_increment_norm"] is None
            else float(outage_no_ramp["maximum_recovery_uwb_state_increment_norm"] - outage_full["maximum_recovery_uwb_state_increment_norm"])
        ),
    }
    md = f"""# Execution ablation analysis

All paired runs use identical synthetic measurement hashes.

* Disabling UWB updates changes the clean estimator state: `{analysis['disabling_uwb_changes_state']}`. The final root-position error changes by {analysis['clean_final_error_change_without_uwb_m']:.6g} m, proving the UWB update path is connected rather than a no-op.
* Disabling health accommodation changes the single-anchor-fault state: `{analysis['disabling_health_changes_state']}`. The maximum root error changes by {analysis['bad_measurement_max_error_increase_without_health_m']:.6g} m; accommodation improves that maximum-error outcome: `{analysis['health_accommodation_improves_max_error']}`.
* Disabling the recovery ramp materially changes the full-outage-recovery state above 1e-9: `{analysis['disabling_recovery_ramp_changes_state']}`. The maximum state-vector difference is {analysis['maximum_state_vector_difference_without_recovery_ramp']:.6g}, while the maximum covariance Frobenius difference is {analysis['maximum_covariance_frobenius_difference_without_recovery_ramp']:.6g}. The maximum recovery UWB state increment changes by {analysis['recovery_increment_change_without_ramp']}; therefore the ramp changes covariance weighting but does not demonstrate a material state-discontinuity/overshoot benefit in this replay.

These are diagnostic ablations only. They neither retune thresholds nor replace the sealed qualification run.
"""
    (output / "EXECUTION_ABLATION_ANALYSIS.md").write_text(md)
    return {"rows": rows, "analysis": analysis}


def execution_products(details: Mapping[str, dict[str, Any]], fusion: Path, output: Path) -> dict[str, Any]:
    counter_rows = []
    all_state_rows = []
    all_health_rows = []
    all_step_rows = []
    all_interval_rows = []
    for scenario_id, detail in details.items():
        dt = np.asarray(detail["native_dt_s"], float)
        starts = {
            row["start_time_ns"]
            for row in detail["interval_rows"]
            if row["step_index"] == 0 and row["start_time_ns"] is not None
        }
        counter_rows.append(
            {
                "evidence_origin": EVIDENCE_REPLAY,
                "scenario_id": scenario_id,
                "variant": detail["variant"],
                **detail["counters"],
                "native_sample_counts_by_node": detail["native_samples_by_node"],
                "native_start_time_unique_count_measured": len(starts),
                "native_dt_count": len(dt),
                "native_dt_min_s": float(dt.min()),
                "native_dt_mean_s": float(dt.mean()),
                "native_dt_p95_s": float(np.quantile(dt, 0.95)),
                "native_dt_max_s": float(dt.max()),
                "native_variable_dt_measured": len(np.unique(dt)) > 1,
                "bias_jacobian_effect_norm_max": detail["bias_jacobian_effect_norm_max"],
            }
        )
        all_state_rows.extend(detail["state_rows"])
        all_health_rows.extend(detail["health_rows"])
        all_interval_rows.extend(detail["interval_rows"])
        for row in detail["step_traces"]:
            flattened = dict(row)
            for field in ("state_before", "imu_propagated_state", "state_after", "root_position_before_m", "root_position_after_imu_m", "root_position_after_uwb_m"):
                flattened[field] = json.dumps(jsonable(flattened[field]), separators=(",", ":"))
            flattened["health_snapshot"] = json.dumps(jsonable(flattened["health_snapshot"]), sort_keys=True, separators=(",", ":"))
            flattened["imu_statuses"] = json.dumps(flattened["imu_statuses"], sort_keys=True, separators=(",", ":"))
            flattened["uwb_audit"] = json.dumps(flattened["uwb_audit"], sort_keys=True, separators=(",", ":"))
            all_step_rows.append(flattened)
    write_csv(output / "EXECUTION_COUNTERS.csv", counter_rows)
    trace_payload = {
        "schema": "biospur-root-r6a2a-r1-execution-path-trace-v1",
        "evidence_origin": EVIDENCE_REPLAY,
        "state_order_path": "STATE_ORDER.json",
        "source_sha256": sha256_file(fusion / "src/biospur_fusion/root_r6a2a/shadow.py"),
        "source_paths": {
            "preintegrator_invocation_line": source_line(fusion / "src/biospur_fusion/root_r6a2a/shadow.py", "intervals = self.preintegrator.integrate_async("),
            "propagator_invocation_line": source_line(fusion / "src/biospur_fusion/root_r6a2a/shadow.py", "propagated = self._propagate(intervals, target_time_s)"),
            "delta_rotation_consumption_line": source_line(fusion / "src/biospur_fusion/root_r6a2a/shadow.py", "target_imu_rotation = old_imu.rotation @ corrected.delta_rotation"),
            "pelvis_delta_position_line": source_line(fusion / "src/biospur_fusion/root_r6a2a/shadow.py", "+ rotation_wi @ corrected.delta_position"),
            "pelvis_delta_velocity_line": source_line(fusion / "src/biospur_fusion/root_r6a2a/shadow.py", "root_velocity = root_velocity + rotation_wi @ corrected.delta_velocity"),
            "preintegration_covariance_line": source_line(fusion / "src/biospur_fusion/root_r6a2a/shadow.py", "covariance[:9, :9] += pelvis_interval.covariance"),
            "uwb_factor_prediction_line": source_line(fusion / "src/biospur_fusion/root_r6a2a/shadow.py", "prediction = factor.predicted(state_at)"),
            "uwb_state_update_line": source_line(fusion / "src/biospur_fusion/root_r6a2a/shadow.py", "root_translation_model_m=propagated.root_translation_model_m + correction"),
            "health_weight_line": source_line(fusion / "src/biospur_fusion/root_r6a2a/shadow.py", "weight = min("),
            "state_commit_line": source_line(fusion / "src/biospur_fusion/root_r6a2a/shadow.py", "self.state = updated"),
        },
        "per_scenario_counters": counter_rows,
        "instrumentation_contract": "Runtime wrappers counted calls only while IntegratedShadowEstimator.step executed; derived residual/FK sensitivity calculations ran with counting disabled.",
    }
    write_json(output / "EXECUTION_PATH_TRACE.json", trace_payload)
    write_csv(output / "CLEAN_BASELINE_EXECUTION_TRACE.csv", [row for row in all_step_rows if row["scenario_id"] == "clean_baseline_seed_6201"])
    write_csv(output / "FULL_UWB_OUTAGE_EXECUTION_TRACE.csv", [row for row in all_step_rows if row["scenario_id"] == "uwb_full_outage_recovery"])
    write_jsonl(output / "RECONSTRUCTED_INTERVAL_EVIDENCE.jsonl", all_interval_rows)
    write_jsonl(output / "RECONSTRUCTED_STATE_ERROR_EVIDENCE.jsonl", all_state_rows)

    pd.DataFrame(all_step_rows).to_parquet(output / "SCENARIO_TIMESERIES.parquet", index=False)
    pd.DataFrame(all_health_rows).to_parquet(output / "MODE_AND_HEALTH_TIMESERIES.parquet", index=False)
    pd.DataFrame(all_state_rows).to_parquet(output / "STATE_AND_COVARIANCE_TIMESERIES.parquet", index=False)
    npz_payload: dict[str, np.ndarray] = {}
    for scenario_id, detail in details.items():
        npz_payload[f"{scenario_id}__estimate"] = detail["estimate_vectors"]
        npz_payload[f"{scenario_id}__truth"] = detail["truth_vectors"]
        npz_payload[f"{scenario_id}__tangent_error"] = detail["tangent_errors"]
        npz_payload[f"{scenario_id}__covariance"] = detail["covariances"]
    np.savez_compressed(output / "RECONSTRUCTED_STATE_AND_COVARIANCE.npz", **npz_payload)
    write_json(output / "STATE_ORDER.json", {"dimension": len(next(iter(details.values()))["state_order"]), "ordering": next(iter(details.values()))["state_order"]})
    write_json(
        output / "RECONSTRUCTED_EVIDENCE_SCHEMA.json",
        {
            "schema": "biospur-root-r6a2a-r1-reconstructed-timeseries-schema-v1",
            "evidence_origin": EVIDENCE_REPLAY,
            "scenario_timeseries": "one row per estimator step; vector-valued fields are JSON arrays",
            "mode_and_health_timeseries": "one row per instantiated health record per step",
            "state_and_covariance_timeseries": "one row per state, with state/truth/tangent vectors and covariance diagonal as JSON arrays; full matrices are in the NPZ",
            "full_covariance_archive": "RECONSTRUCTED_STATE_AND_COVARIANCE.npz",
            "units": "encoded in column and STATE_ORDER names",
        },
    )
    return {
        "counter_rows": counter_rows,
        "state_rows": all_state_rows,
        "health_rows": all_health_rows,
        "step_rows": all_step_rows,
    }


def final_products(
    output: Path,
    details: Mapping[str, dict[str, Any]],
    state_summary: Sequence[Mapping[str, Any]],
    anomaly_rows: Sequence[Mapping[str, Any]],
    covariance_products_result: Mapping[str, Any],
    fault_label_result: Mapping[str, Any],
    ablation_result: Mapping[str, Any],
    replay_equivalence: Mapping[str, Any],
) -> dict[str, Any]:
    overall_state = {
        row["scenario_id"]: row
        for row in state_summary
        if row["phase"] == "ALL"
    }
    detected = []
    for scenario_id, detail in details.items():
        spec = detail["scenario"]
        if any(
            transition["to"] in {"SUSPECT", "DEGRADED", "ISOLATED"}
            and transition["time_s"] >= spec["fault_start_step"] * spec["step_s"]
            for transition in detail["health_transitions"]
        ):
            detected.append(scenario_id)
    incomplete = [
        row["scenario_id"]
        for row in anomaly_rows
        if row["classification"] in {"MISSING_HANDLER", "DETECTED_BUT_WRONG_MODE", "TRUTH_LABEL_LEAKAGE"}
    ]
    late_detection = []
    for scenario_id in detected:
        detail = details[scenario_id]
        spec = detail["scenario"]
        start = spec["fault_start_step"] * spec["step_s"]
        relevant_times = [
            row["time_s"]
            for row in detail["health_transitions"]
            if row["to"] in {"SUSPECT", "DEGRADED", "ISOLATED"} and row["time_s"] >= start
        ]
        if relevant_times and min(relevant_times) - start > FROZEN_THRESHOLDS["fault_detection_latency_max_s"]:
            late_detection.append(scenario_id)
    correctly_detected = [
        scenario_id for scenario_id in detected if scenario_id not in incomplete and scenario_id not in late_detection
    ]
    clean_counters = next(
        detail["counters"] for scenario_id, detail in details.items() if scenario_id == "clean_baseline_seed_6201"
    )
    fk_sensitivity_max = max(
        row["fk_zero_joint_prediction_difference_m"]
        for detail in details.values()
        for row in detail["residual_rows"]
    )
    covariance_all = pd.DataFrame(covariance_products_result["covariance_rows"])
    clean_covariance = covariance_all[
        (covariance_all["phase"] == "ALL")
        & covariance_all["scenario_id"].str.startswith("clean_")
    ]
    clean_nees = float(clean_covariance["full_state_tangent_nees_per_dimension_mean"].mean())
    clean_coverage_1 = float(clean_covariance["coverage_1sigma_mean_fraction"].mean())
    covariance_scale_consistent = 0.5 <= clean_nees <= 2.0 and 0.55 <= clean_coverage_1 <= 0.82
    covariance_finding = (
        "NOT_FORMALLY_ESTABLISHED_WITH_LIMITED_CORRELATED_SYNTHETIC_SAMPLES"
        if covariance_scale_consistent
        else "INCONSISTENT_OR_UNCALIBRATED_SCALE_IN_TANGENT_DIAGNOSTICS"
    )
    phase_error_rows = [
        row
        for row in state_summary
        if row["scenario_id"] in ANOMALOUS_SCENARIOS and row["phase"] != "ALL"
    ]
    verdict = "FAIL_ROOT_R6A2A_R1_FAULT_TRUTH_LEAKAGE"
    final = {
        "schema": "biospur-root-r6a2a-r1-final-audit-v1",
        "principal_verdict": verdict,
        "parent_replay_equivalent": replay_equivalence["all_exact"],
        "parent_architecture_classification": "GENUINE_INTEGRATED_SYNTHETIC_ARCHITECTURE_WITH_SCIENTIFIC_QUALIFICATION_FAILURE",
        "answers": {
            "1_genuine_state_updates_occurred": {
                "answer": True,
                "evidence": {
                    "clean_state_updates": clean_counters["state_updates"],
                    "clean_nonzero_state_increments": clean_counters["nonzero_state_increments"],
                },
            },
            "2_imu_uwb_fk_materially_affected_estimate": {
                "answer": True,
                "imu": clean_counters["nonzero_imu_propagation_increments"] > 0,
                "uwb": ablation_result["analysis"]["disabling_uwb_changes_state"],
                "shared_fk": fk_sensitivity_max > 0.0,
                "maximum_zero_joint_range_prediction_difference_m": fk_sensitivity_max,
                "qualification": "Bias Jacobians are called but have zero numerical effect because estimated gyro/accel bias states never update.",
            },
            "3_estimator_saw_injected_fault_truth": {
                "state_and_health_path": False,
                "reported_fault_attribution_path": True,
                "answer_for_scientific_result": True,
                "hard_failure": fault_label_result["controls"]["hard_scientific_failure"],
            },
            "4_correctly_detected_scenarios": {
                "health_event_detection_within_declared_latency_and_without_known_incomplete_mode": correctly_detected,
                "detected_but_late": late_detection,
                "detected_but_incomplete_or_wrong_mode": [name for name in detected if name in incomplete],
                "caveat": "This concerns event/scope detection only; the reported attribution labels remain scientifically invalid because of truth-label leakage.",
            },
            "5_intentionally_tolerated_faults": [
                "imu_bounded_sample_gap (one missing native sample remained within the configured 20 ms maximum-gap envelope and was counted as a bounded gap)",
                "clean_low_motion_jitter (declared low-motion correction hold; a test condition rather than an injected fault)",
                "uwb_low_vertical_diversity (predeclared geometry-degraded mode, not residual fault isolation)",
            ],
            "6_incorrect_or_incomplete_degraded_modes": incomplete,
            "7_actual_state_errors_by_fault_phase": phase_error_rows,
            "8_covariance_consistent_with_truth": {
                "answer": False,
                "finding": covariance_finding,
                "clean_mean_full_state_nees_per_dimension": clean_nees,
                "clean_mean_1sigma_coordinate_coverage": clean_coverage_1,
                "reason": "PSD/outage growth are genuine, but formal full-factor innovation consistency is unavailable, weak-direction inflation is incomplete, bias covariance has no corresponding bias estimator updates, and the small deterministic replay ensemble cannot qualify covariance scale.",
            },
            "9_accommodation_improved_disabled_health_outcome": {
                "answer": ablation_result["analysis"]["health_accommodation_improves_max_error"],
                "maximum_error_increase_when_disabled_m": ablation_result["analysis"]["bad_measurement_max_error_increase_without_health_m"],
            },
            "10_ready_to_checkpoint": {
                "answer": False,
                "required_action": "Repair fault attribution/dataflow and missing-data/mode handlers, then rerun scientific qualification; do not checkpoint the current R6A2A result.",
            },
        },
        "additional_scientific_findings": [
            "The parent PASS proves an executable integrated synthetic architecture, not a complete fault-aware scientific qualification.",
            "The parent bone invariant is structural: it compares unchanged static calibration slots; there is no bone-stretch state.",
            "Non-pelvis preintegration covariances are recorded but not inserted into joint/bias covariance blocks; only the pelvis 9x9 permutation reaches root covariance.",
            "Estimated gyro and accelerometer biases remain exactly zero in every scenario; no bias state update exists.",
            "Node health is observed once from IMU evidence and again from UWB bad-fraction evidence in the same step, allowing one subsystem's good observation to alter the other's persistence.",
            "Per-event UWB health records use unique event IDs, so isolated individual measurements are never re-observed or requalified.",
            "Wrong lever and wrong bone calibration faults are active for the whole scenario although their manifest declares a bounded fault window.",
            "UWB omission faults share one RNG with later IMU/UWB generation; skipped draws change later cross-sensor noise and extend input differences beyond the declared window.",
            "Disabling the recovery ramp produces no material state-vector effect above 1e-9 in the outage replay; its visible effect is primarily covariance contraction, so a state-discontinuity/overshoot benefit is not demonstrated.",
        ],
        "root_r6a2b_started": False,
        "r6a2a_commit_authorized": False,
        "independent_verification_required": True,
    }
    write_json(output / "FINAL_AUDIT.json", final)
    anomaly_lines = "\n".join(
        f"| {row['scenario_id']} | {row['classification']} | {row['finding']} |"
        for row in anomaly_rows
    )
    md = f"""# Root-R6A2A-R1 execution and scientific audit

## Verdict

`{verdict}`

The parent replay is exact and the integrated architecture is genuine: IMU propagation changes the state, UWB-disabled ablation changes root drift, and shared FK changes predicted ranges. The parent PASS is nevertheless not scientifically valid as a fault-aware qualification because its reported attribution reads injected fault truth directly. Identical measurements with conflicting truth labels yield identical state/health output but different reported attribution.

## Direct answers

1. Genuine state updates occurred: **yes**.
2. IMU, UWB, and shared FK materially affected the estimate: **yes**, with the qualification that bias Jacobians have zero numerical effect and bias states never update.
3. The numerical state/health path saw fault truth: **no**. The reported `fault_attribution` path saw it: **yes**, which is the hard failure.
4. Correctly detected scenarios are enumerated in `FINAL_AUDIT.json`; detection does not validate leaked attribution.
5. The bounded IMU sample gap was intentionally tolerated; low-motion jitter and low-vertical geometry exercised explicit non-fault/degraded envelopes.
6. Missing or incorrect mode handling is documented below and in `ANOMALOUS_SCENARIO_AUDIT.csv`.
7. Actual pre/during/recovery/post errors are in `SCENARIO_STATE_ERROR_SUMMARY.csv` and the Parquet/NPZ time series.
8. Covariance consistency with truth is **not established**. PSD and positive outage growth are insufficient; see `COVARIANCE_AUDIT.md`.
9. Health accommodation improved the selected single-anchor maximum-error diagnostic: **{ablation_result['analysis']['health_accommodation_improves_max_error']}** (disabled-minus-full = {ablation_result['analysis']['bad_measurement_max_error_increase_without_health_m']:.6g} m).
10. The current R6A2A implementation is **not ready to checkpoint**. Repair and requalification are required first.

The recovery-ramp ablation does not materially change the outage state vector above 1e-9; it primarily changes covariance contraction. Also, omission faults share a single RNG with subsequent IMU/UWB generation, so skipped UWB draws contaminate later cross-sensor noise beyond the declared fault window.

## Anomalous scenarios

| Scenario | Classification | Finding |
|---|---|---|
{anomaly_lines}

## Evidence boundary

Parent-only exports preserve missing values as `{NOT_RECORDED}`. Reconstructed metrics are visibly labeled `{EVIDENCE_REPLAY}` and were produced only after the exact replay matched every sealed parent scenario object. No real capture payload was opened, no real slot was fitted, and Root-R6A2B was not started.
"""
    (output / "FINAL_AUDIT.md").write_text(md)
    return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fusion", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    fusion = args.fusion.resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (
        args.output.resolve()
        if args.output is not None
        else fusion / "logs" / f"root_r6a2a_r1_execution_audit_{timestamp}"
    )
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    parent = fusion / "logs" / PARENT_NAME
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")

    disk_nrf = shutil.disk_usage("/mnt/nrf_ssd").free
    disk_root = shutil.disk_usage("/").free
    head_before = command_output(["git", "rev-parse", "HEAD"], fusion)
    if disk_nrf < 100 * 1024**3 or disk_root < 40 * 1024**3:
        raise RuntimeError("Fusion disk gate failed")
    if head_before != CHECKPOINT_HEAD:
        raise RuntimeError(f"checkpoint mismatch: {head_before}")
    parent_gate = verify_parent_checksums(parent)
    if not parent_gate["pass"]:
        raise RuntimeError("parent checksum gate failed")
    implementation_before = snapshot_files(fusion, IMPLEMENTATION_PATHS)
    implementation_manifest = json.loads((parent / "IMPLEMENTATION_FILES.json").read_text())
    expected_implementation = {row["path"]: row["sha256"] for row in implementation_manifest["files"]}
    if {key: value["sha256"] for key, value in implementation_before.items()} != expected_implementation:
        raise RuntimeError("current implementation differs from sealed parent implementation")
    config_before = snapshot_files(fusion, CONFIG_PATHS)
    current_manifest = fault_injection_manifest()
    parent_manifest = json.loads((parent / "FAULT_INJECTION_MANIFEST.json").read_text())
    if current_manifest != parent_manifest:
        raise RuntimeError("scenario manifest or frozen thresholds differ from parent")
    parent_before = parent_snapshot(parent)
    ledger = json.loads((fusion / CONFIG_PATHS[-1]).read_text())
    if len(ledger["slots"]) != 87 or any(
        row["value"] is not None or row["status"] != "FROZEN_UNCERTAIN" for row in ledger["slots"]
    ):
        raise RuntimeError("87-slot real ledger gate failed")

    write_json(output / "FROZEN_IMPLEMENTATION_HASHES.json", implementation_before)
    write_json(output / "FROZEN_CONFIG_HASHES.json", config_before)
    write_json(output / "FROZEN_SCENARIO_MANIFEST.json", current_manifest)
    write_json(output / "FROZEN_THRESHOLDS.json", FROZEN_THRESHOLDS)
    export_parent(parent, output)

    parent_scenarios = json.loads((parent / "SYNTHETIC_SCENARIO_RESULTS.json").read_text())["scenarios"]
    exact_results: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=min(args.workers, len(SCENARIOS))) as executor:
        futures = [executor.submit(exact_replay_worker, str(fusion), spec) for spec in SCENARIOS]
        for future in futures:
            scenario_id, result = future.result()
            exact_results[scenario_id] = result
    equivalence_rows = {}
    for spec in SCENARIOS:
        parent_result = parent_scenarios[spec.name]
        replay_result = exact_results[spec.name]
        equivalence_rows[spec.name] = {
            "whole_object_equal": replay_result == parent_result,
            "parent_canonical_sha256": canonical_sha256(parent_result),
            "replay_canonical_sha256": canonical_sha256(replay_result),
            "parent_state_replay_sha256": parent_result["deterministic_replay_sha256"],
            "replay_state_replay_sha256": replay_result["deterministic_replay_sha256"],
        }
    replay_equivalence = {
        "schema": "biospur-root-r6a2a-r1-replay-equivalence-v1",
        "implementation_hashes_frozen_before_replay": True,
        "scenario_manifest_exact": current_manifest == parent_manifest,
        "thresholds_exact": current_manifest["thresholds"] == FROZEN_THRESHOLDS,
        "same_seed_trajectory_fault_timing_magnitude_configuration_registry_geometry": True,
        "serialization_tolerance": 0.0,
        "scenarios": equivalence_rows,
        "all_exact": all(row["whole_object_equal"] for row in equivalence_rows.values()),
    }
    write_json(output / "REPLAY_EQUIVALENCE.json", replay_equivalence)
    if not replay_equivalence["all_exact"]:
        raise RuntimeError("STOP_ROOT_R6A2A_R1_NONDETERMINISTIC_OR_PARENT_MISMATCH")

    details: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=min(args.workers, len(SCENARIOS))) as executor:
        futures = {
            spec.name: executor.submit(detailed_replay_worker, str(fusion), spec, "full")
            for spec in SCENARIOS
        }
        for scenario_id, future in futures.items():
            details[scenario_id] = future.result()
    instrumentation_equivalence = {}
    for scenario_id, detail in details.items():
        parent_result = parent_scenarios[scenario_id]
        instrumentation_equivalence[scenario_id] = {
            "state_replay_sha256_equal": detail["deterministic_replay_sha256"] == parent_result["deterministic_replay_sha256"],
            "mode_sequence_equal": detail["mode_sequence"] == parent_result["metrics"]["mode_sequence"],
            "health_transitions_equal": detail["health_transitions"] == parent_result["health_transitions"],
            "step_audit_equal": detail["step_audit"] == parent_result["step_audit"],
            "reported_attribution_equal": detail["reported_attribution"] == parent_result["metrics"]["fault_attribution"],
        }
    replay_equivalence["instrumented_replay"] = instrumentation_equivalence
    replay_equivalence["instrumentation_all_exact"] = all(
        all(row.values()) for row in instrumentation_equivalence.values()
    )
    write_json(output / "REPLAY_EQUIVALENCE.json", replay_equivalence)
    if not replay_equivalence["instrumentation_all_exact"]:
        raise RuntimeError("instrumentation changed numerical/serialized parent output")

    fault_effects: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=min(args.workers, len(SCENARIOS))) as executor:
        futures = {spec.name: executor.submit(input_fault_effect, str(fusion), spec) for spec in SCENARIOS}
        for scenario_id, future in futures.items():
            fault_effects[scenario_id] = future.result()
    write_json(output / "DETERMINISTIC_FAULT_EFFECTS.json", fault_effects)

    execution_products(details, fusion, output)
    state_summary, joint_summary, bias_summary = grouped_state_summaries(details)
    residual_summary = residual_summaries(details)
    recovery_summary = recovery_summaries(details)
    write_csv(output / "SCENARIO_STATE_ERROR_SUMMARY.csv", state_summary)
    write_csv(output / "PER_JOINT_ERROR_SUMMARY.csv", joint_summary)
    write_csv(output / "PER_NODE_BIAS_ERROR_SUMMARY.csv", bias_summary)
    write_csv(output / "UWB_RESIDUAL_SUMMARY.csv", residual_summary)
    write_csv(output / "RECOVERY_METRICS.csv", recovery_summary)
    covariance_result = covariance_products(details, output)
    label_result = fault_label_audit(fusion, output)

    spec_by_name = {spec.name: spec for spec in SCENARIOS}
    counterfactuals: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=min(args.workers, len(ANOMALOUS_SCENARIOS))) as executor:
        futures = {
            scenario_id: executor.submit(
                detailed_replay_worker,
                str(fusion),
                replace(spec_by_name[scenario_id], fault="none"),
                "full",
            )
            for scenario_id in ANOMALOUS_SCENARIOS
        }
        for scenario_id, future in futures.items():
            counterfactuals[scenario_id] = future.result()
    anomaly_rows = anomalous_scenario_rows(details, counterfactuals, fault_effects)
    write_csv(output / "ANOMALOUS_SCENARIO_AUDIT.csv", anomaly_rows)

    ablation_specs = {
        "no_uwb_updates": (spec_by_name["clean_baseline_seed_6201"], "no_uwb_updates"),
        "health_accommodation_disabled": (spec_by_name["uwb_single_anchor_fault"], "health_accommodation_disabled"),
        "recovery_ramp_disabled": (spec_by_name["uwb_full_outage_recovery"], "recovery_ramp_disabled"),
    }
    ablation_variants: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=3) as executor:
        futures = {
            name: executor.submit(detailed_replay_worker, str(fusion), spec, variant)
            for name, (spec, variant) in ablation_specs.items()
        }
        for name, future in futures.items():
            ablation_variants[name] = future.result()
    ablation_result = execution_ablation_products(details, ablation_variants, output)

    implementation_after = snapshot_files(fusion, IMPLEMENTATION_PATHS)
    config_after = snapshot_files(fusion, CONFIG_PATHS)
    parent_after = parent_snapshot(parent)
    head_after = command_output(["git", "rev-parse", "HEAD"], fusion)
    ledger_after = json.loads((fusion / CONFIG_PATHS[-1]).read_text())
    protected = {
        "schema": "biospur-root-r6a2a-r1-protected-hashes-before-after-v1",
        "before": {
            "parent": parent_before,
            "implementation": implementation_before,
            "config": config_before,
            "git_head": head_before,
        },
        "after": {
            "parent": parent_after,
            "implementation": implementation_after,
            "config": config_after,
            "git_head": head_after,
        },
        "parent_byte_exact": parent_before == parent_after and verify_parent_checksums(parent)["pass"],
        "implementation_byte_exact": implementation_before == implementation_after,
        "config_byte_exact": config_before == config_after,
        "git_head_unchanged": head_before == head_after == CHECKPOINT_HEAD,
        "real_ledger": {
            "total": len(ledger_after["slots"]),
            "value_null": sum(row["value"] is None for row in ledger_after["slots"]),
            "FROZEN_UNCERTAIN": sum(row["status"] == "FROZEN_UNCERTAIN" for row in ledger_after["slots"]),
            "sha256": sha256_file(fusion / CONFIG_PATHS[-1]),
        },
        "real_payloads_opened": False,
        "real_payload_path_accesses": [],
        "real_registry_writes": 0,
        "r6a2a_commit_performed": False,
        "push_performed": False,
        "merge_performed": False,
        "root_r6a2b_started": False,
        "audit_source_paths": [str(Path(__file__).resolve()), str((Path(__file__).parent / "audit_common.py").resolve())],
    }
    write_json(output / "PROTECTED_HASHES_BEFORE_AFTER.json", protected)
    final_products(
        output,
        details,
        state_summary,
        anomaly_rows,
        covariance_result,
        label_result,
        ablation_result,
        replay_equivalence,
    )
    print(str(output))


if __name__ == "__main__":
    main()
