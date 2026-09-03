"""Evidence-driven R6A2A-R2 estimator.

The public step method accepts exactly one authority object.  Fault injection
and scoring objects are intentionally neither imported nor accepted here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from biospur_fusion.imu.preintegration import (
    NativeTimePreintegrator,
    NoiseParameters,
    PreintegratedInterval,
    PreintegrationStatus,
    PreintegratorConfig,
)
from biospur_fusion.root_r6a0.body import BodyModel, KeyframeState
from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a1c.adapter import CORRECT_NODE_MAP
from biospur_fusion.root_r6a2a.shadow import GRAVITY_W, corrected_body_model, interpolate_state

from .contracts import (
    AffectedScope,
    DegradedMode,
    EstimatorInput,
    EstimatorOutput,
    HealthManager,
    HealthState,
    ObservationAccounting,
    ObservationStatus,
    StateLayout,
    SYNTHETIC_PROVENANCE,
    UwbMeasurement,
    mixed_unit_initial_covariance,
)


BAD_PREINTEGRATION = {
    PreintegrationStatus.DUPLICATE_TIMESTAMP,
    PreintegrationStatus.TIME_REVERSAL,
    PreintegrationStatus.BOOT_EPOCH_CHANGE,
    PreintegrationStatus.SATURATION,
    PreintegrationStatus.NONFINITE,
    PreintegrationStatus.INVALID_SAMPLE_STATUS,
    PreintegrationStatus.GAP_EXCEEDS_ENVELOPE,
}

# A synthetic calibration family uses one fixed FK linearization per tag.  The
# actual range direction is still recomputed per event, while this shared-FK
# point Jacobian is reused to keep Monte Carlo qualification tractable.
_POINT_JACOBIAN_CACHE: dict[tuple[str, str], np.ndarray] = {}


def _psd(value: np.ndarray, floor: float = 1e-12) -> np.ndarray:
    symmetric = 0.5 * (np.asarray(value, float) + np.asarray(value, float).T)
    eigenvalues, eigenvectors = np.linalg.eigh(symmetric)
    return (eigenvectors * np.maximum(eigenvalues, floor)) @ eigenvectors.T


def _state_vector(state: KeyframeState, layout: StateLayout) -> np.ndarray:
    return np.concatenate((
        state.root_translation_model_m,
        state.root_rotation_model_rotvec,
        state.root_velocity_model_mps,
        *(state.joint_rotvec[joint] for joint in layout.joint_ids),
        *(state.joint_rate_rad_s[joint] for joint in layout.joint_ids),
        *(state.gyro_bias_rad_s[node] for node in layout.node_ids),
        *(state.accel_bias_mps2[node] for node in layout.node_ids),
    ))


def _apply_increment(state: KeyframeState, layout: StateLayout, increment: np.ndarray) -> KeyframeState:
    dx = np.asarray(increment, float)
    root_rotation = so3_log(so3_exp(state.root_rotation_model_rotvec) @ so3_exp(dx[3:6]))
    joints = {
        joint: so3_log(so3_exp(state.joint_rotvec[joint]) @ so3_exp(dx[layout.joint_orientation(joint)]))
        for joint in layout.joint_ids
    }
    return replace(
        state,
        root_translation_model_m=state.root_translation_model_m + dx[0:3],
        root_rotation_model_rotvec=root_rotation,
        root_velocity_model_mps=state.root_velocity_model_mps + dx[6:9],
        joint_rotvec=joints,
        joint_rate_rad_s={
            joint: state.joint_rate_rad_s[joint] + dx[layout.joint_rate(joint)]
            for joint in layout.joint_ids
        },
        gyro_bias_rad_s={
            node: state.gyro_bias_rad_s[node] + dx[layout.gyro_bias(node)]
            for node in layout.node_ids
        },
        accel_bias_mps2={
            node: state.accel_bias_mps2[node] + dx[layout.accel_bias(node)]
            for node in layout.node_ids
        },
    )


def make_initial_state(model: BodyModel, reference: KeyframeState, low_motion: bool = False) -> tuple[KeyframeState, dict[str, Any]]:
    layout = StateLayout(model.joint_ids, model.imu_ids)
    offset = np.zeros(3) if low_motion else np.array([0.055, -0.035, 0.018])
    # The synthetic initializer applies a declared deterministic axis-error
    # envelope.  Its prior must use those axis second moments; an unrelated
    # isotropic 60 mm covariance survives weak vertical geometry and is not the
    # uncertainty distribution produced by this initializer.  Low-motion
    # starts at truth, but retains a finite 1 mm numerical/initialization floor.
    root_axis_standard_deviations = np.maximum(np.abs(offset), 1.0e-3)
    covariance, contract = mixed_unit_initial_covariance(
        layout, root_axis_standard_deviations,
    )
    state = replace(
        reference,
        root_translation_model_m=reference.root_translation_model_m + offset,
        root_velocity_model_mps=reference.root_velocity_model_mps + np.array([0.015, -0.010, 0.0]),
        gyro_bias_rad_s={node: np.zeros(3) for node in model.imu_ids},
        accel_bias_mps2={node: np.zeros(3) for node in model.imu_ids},
        covariance=covariance,
    )
    state.validate(model)
    return state, contract


def all_node_covariance_mapping(model: BodyModel) -> dict[str, Any]:
    layout = StateLayout(model.joint_ids, model.imu_ids)
    incoming = {joint.child: joint.joint_id for joint in model.joints}
    rows: dict[str, Any] = {}
    for node in model.imu_ids:
        segment = CORRECT_NODE_MAP[node]
        if segment == model.root_segment:
            state_blocks = {
                "preint_rotation": [3, 6], "preint_velocity": [6, 9],
                "preint_position": [0, 3],
            }
            dependency = "pelvis/root navigation"
        else:
            joint = incoming[segment]
            state_blocks = {
                "preint_rotation": [layout.joint_orientation(joint).start, layout.joint_orientation(joint).stop],
                "preint_velocity": [layout.joint_rate(joint).start, layout.joint_rate(joint).stop],
                "preint_position": [layout.joint_orientation(joint).start, layout.joint_orientation(joint).stop],
            }
            dependency = f"segment {segment} through incoming joint {joint}"
        state_blocks.update({
            "gyro_bias": [layout.gyro_bias(node).start, layout.gyro_bias(node).stop],
            "accelerometer_bias": [layout.accel_bias(node).start, layout.accel_bias(node).stop],
        })
        rows[node] = {
            "segment": segment, "dependency": dependency,
            "preintegration_order": ["rotation", "velocity", "position", "gyro_bias", "accelerometer_bias"],
            "state_blocks": state_blocks,
            "mapping": "P <- F P F^T + J_node Q_preint J_node^T",
            "cross_covariance_preserved": True,
            "discarded": False,
        }
    return {
        "schema": "biospur-root-r6a2a-r2-all-node-covariance-mapping-v1",
        "state_dimension": 123, "node_count": len(rows), "nodes": rows,
    }


class RepairedShadowEstimator:
    """One ten-node estimator shared by both qualified geometry families."""

    def __init__(self, model: BodyModel, initial_state: KeyframeState):
        if dict(model.identity_mapping) != dict(CORRECT_NODE_MAP):
            raise ValueError("canonical corrected identity mapping required")
        self.model = model
        self.layout = StateLayout(model.joint_ids, model.imu_ids)
        self.state = initial_state
        self.states = [initial_state]
        self.health = HealthManager()
        self.outputs: list[EstimatorOutput] = []
        self.mode_history = [DegradedMode.NORMAL.value]
        self.preintegration_status = Counter()
        self.covariance_mapping_counts = Counter()
        self.raw_gyro_baseline: dict[str, np.ndarray] = {}
        self.raw_accel_baseline: dict[str, np.ndarray] = {}
        self.raw_gyro_history: dict[str, list[np.ndarray]] = {}
        self.bias_update_count = {node: {"gyro": np.zeros(3, int), "accel": np.zeros(3, int)} for node in model.imu_ids}
        self.bias_max_correction = {node: {"gyro": np.zeros(3), "accel": np.zeros(3)} for node in model.imu_ids}
        self.bias_sensitivity = {node: np.zeros((6, 6)) for node in model.imu_ids}
        self.correction_norms: list[float] = []
        self.covariance_traces: list[float] = [float(np.trace(initial_state.covariance))]
        self.root_covariance_traces: list[float] = [float(np.trace(initial_state.covariance[0:3, 0:3]))]
        self.yaw_variances: list[float] = [float(initial_state.covariance[5, 5])]
        self.innovation_rows: list[dict[str, Any]] = []
        self.imu_innovation_rows: list[dict[str, Any]] = []
        self.information_rows: list[dict[str, Any]] = []
        self.covariance_trace_rows: list[dict[str, Any]] = []
        self._last_process_contributions: dict[str, np.ndarray] = {}
        self._last_transition_count = 0
        self._calibration_signature: str | None = None
        noise = {
            node: NoiseParameters(0.020, 0.002, 0.0002, 0.00002, SYNTHETIC_PROVENANCE)
            for node in model.imu_ids
        }
        self.preintegrator = NativeTimePreintegrator(noise, PreintegratorConfig(
            max_gap_s=0.020, missing_sample_threshold_s=0.0075,
            accel_saturation_mps2=80.0, gyro_saturation_rad_s=12.0,
        ))

    def _bias_observer(
        self,
        value: EstimatorInput,
        intervals: Mapping[str, PreintegratedInterval],
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], list[tuple[str, str, bool, str, bool]]]:
        gyro = {node: self.state.gyro_bias_rad_s[node].copy() for node in self.model.imu_ids}
        accel = {node: self.state.accel_bias_mps2[node].copy() for node in self.model.imu_ids}
        evidence: list[tuple[str, str, bool, str, bool]] = []
        raw_gyro_means = {
            node: np.mean([sample.gyro_rad_s for sample in value.imu_streams.get(node, ())], axis=0)
            for node in self.model.imu_ids if value.imu_streams.get(node, ())
        }
        low_motion_observable = bool(raw_gyro_means) and float(np.median([
            np.linalg.norm(vector) for vector in raw_gyro_means.values()
        ])) < 0.080
        for node in self.model.imu_ids:
            interval = intervals[node]
            samples = value.imu_streams.get(node, ())
            hard = interval.status in BAD_PREINTEGRATION
            motion_bad = False
            reason = interval.status.value
            if interval.valid and samples:
                raw_gyro = np.mean([sample.gyro_rad_s for sample in samples], axis=0)
                raw_accel = np.mean([sample.accel_mps2 for sample in samples], axis=0)
                if node not in self.raw_gyro_baseline:
                    self.raw_gyro_baseline[node] = raw_gyro.copy()
                    self.raw_accel_baseline[node] = raw_accel.copy()
                    self.raw_gyro_history[node] = [raw_gyro.copy()]
                gyro_departure = raw_gyro - self.raw_gyro_baseline[node]
                accel_departure = raw_accel - self.raw_accel_baseline[node]
                history = self.raw_gyro_history[node]
                predicted = raw_gyro if len(history) < 2 else 2.0 * history[-1] - history[-2]
                prediction_error = raw_gyro - predicted
                motion_bad = float(np.linalg.norm(prediction_error)) > 0.24
                reason = f"ANGULAR_PREDICTION_ERROR={np.linalg.norm(prediction_error):.9g}"
                self.imu_innovation_rows.append({
                    "step": value.step_index, "node_id": node,
                    "angular_prediction_error_rad_s": prediction_error.tolist(),
                    "norm_rad_s": float(np.linalg.norm(prediction_error)),
                })
                if value.options.bias_updates_enabled and not motion_bad and low_motion_observable:
                    # Persistent innovations above normal smooth-motion change
                    # drive only locally sensitive components.  Gyro bias is
                    # observable here because the system itself detects the
                    # declared low-angular-rate condition from all ten IMUs.
                    for kind, departure, estimate, threshold, gain in (
                        ("gyro", raw_gyro, gyro, 0.003, 0.32),
                        ("accel", accel_departure, accel, 0.18, 0.18),
                    ):
                        active = np.abs(departure) > threshold
                        innovation = departure - estimate[node]
                        correction = np.where(active, gain * innovation, 0.0)
                        correction = np.clip(correction, -0.040 if kind == "gyro" else -0.12,
                                             0.040 if kind == "gyro" else 0.12)
                        estimate[node] = estimate[node] + correction
                        self.bias_update_count[node][kind] += active.astype(int)
                        self.bias_max_correction[node][kind] = np.maximum(
                            self.bias_max_correction[node][kind], np.abs(correction),
                        )
                    self.raw_gyro_baseline[node] = 0.99 * self.raw_gyro_baseline[node] + 0.01 * raw_gyro
                    self.raw_accel_baseline[node] = 0.99 * self.raw_accel_baseline[node] + 0.01 * raw_accel
                history.append(raw_gyro.copy())
                if len(history) > 2:
                    del history[:-2]
            evidence.append(("imu_health", node, (not interval.valid) or motion_bad, reason, hard))
            clock_bad = any(
                row.modality == "IMU" and row.node_id == node and not row.clock_valid
                for row in value.expected_schedule
            )
            evidence.append(("clock_health", node, clock_bad, "CLOCK_SCHEDULE_VALIDITY", clock_bad))
        return gyro, accel, evidence

    def _node_mapping(self, node: str, interval: PreintegratedInterval) -> np.ndarray:
        mapping = np.zeros((self.layout.dimension, 15))
        segment = CORRECT_NODE_MAP[node]
        incoming = {joint.child: joint.joint_id for joint in self.model.joints}
        if segment == self.model.root_segment:
            mapping[3:6, 0:3] = np.eye(3)
            mapping[6:9, 3:6] = np.eye(3)
            mapping[0:3, 6:9] = np.eye(3)
        else:
            joint = incoming[segment]
            orient = self.layout.joint_orientation(joint)
            rate = self.layout.joint_rate(joint)
            lever = max(0.20, float(np.linalg.norm(self.state.root_translation_model_m)))
            mapping[orient, 0:3] = np.eye(3)
            mapping[rate, 3:6] = np.eye(3) / lever
            mapping[orient, 6:9] += np.eye(3) / lever
        mapping[self.layout.gyro_bias(node), 9:12] = np.eye(3)
        mapping[self.layout.accel_bias(node), 12:15] = np.eye(3)
        self.bias_sensitivity[node][0:3, 0:3] += np.abs(interval.jacobian_rotation_gyro_bias)
        self.bias_sensitivity[node][3:6, 3:6] += np.abs(interval.jacobian_velocity_accel_bias)
        return mapping

    def _propagate(
        self,
        value: EstimatorInput,
        intervals: Mapping[str, PreintegratedInterval],
        gyro_bias: Mapping[str, np.ndarray],
        accel_bias: Mapping[str, np.ndarray],
    ) -> KeyframeState:
        previous = self.state
        previous_predictions = self.model.all_predictions(previous, value.calibration)
        segment_rotations = {key: pose.rotation.copy() for key, pose in previous_predictions["segments"].items()}
        covariance = previous.covariance.copy()
        process_contributions: dict[str, np.ndarray] = {}
        valid = 0
        for node in self.model.imu_ids:
            interval = intervals[node]
            self.preintegration_status[interval.status.value] += 1
            if not interval.valid or self.health.channel("imu_health", node).weight <= 0.0:
                continue
            valid += 1
            if value.options.bias_jacobians_enabled:
                delta = interval.bias_corrected(gyro_bias[node], accel_bias[node])
            else:
                delta = interval.bias_corrected(interval.reference_gyro_bias_rad_s, interval.reference_accel_bias_mps2)
            old_imu = previous_predictions["imus"][node]
            extrinsic = value.calibration.pose(f"imu_extrinsic:{node}")
            segment_rotations[CORRECT_NODE_MAP[node]] = old_imu.rotation @ delta.delta_rotation @ extrinsic.rotation.T
            mapping = self._node_mapping(node, interval)
            mapped = mapping @ interval.covariance @ mapping.T
            process_contributions[f"preintegration:{node}"] = mapped
            if (
                value.options.preintegration_covariance_enabled
                and value.options.excluded_preintegration_covariance_node != node
            ):
                covariance += mapped
            self.covariance_mapping_counts[node] += 1

        pelvis = "BSFC2CC"
        root_position = previous.root_translation_model_m.copy()
        root_velocity = previous.root_velocity_model_mps.copy()
        interval = intervals[pelvis]
        dt = max(1e-9, value.interval_end_s - value.interval_start_s)
        if interval.valid and self.health.channel("imu_health", pelvis).weight > 0.0:
            if value.options.bias_jacobians_enabled:
                delta = interval.bias_corrected(gyro_bias[pelvis], accel_bias[pelvis])
            else:
                delta = interval.bias_corrected(interval.reference_gyro_bias_rad_s, interval.reference_accel_bias_mps2)
            rotation_wi = previous_predictions["imus"][pelvis].rotation
            root_position = root_position + root_velocity * interval.duration_s + rotation_wi @ delta.delta_position + 0.5 * GRAVITY_W * interval.duration_s**2
            root_velocity = root_velocity + rotation_wi @ delta.delta_velocity + GRAVITY_W * interval.duration_s
        else:
            root_position = root_position + root_velocity * dt
            invalid_pelvis = np.zeros_like(covariance)
            invalid_pelvis[0:3, 0:3] = np.eye(3) * 7.5e-4
            invalid_pelvis[3:6, 3:6] = np.eye(3) * 2.5e-4
            process_contributions["invalid_pelvis"] = invalid_pelvis
            if value.options.invalid_pelvis_covariance_enabled:
                covariance += invalid_pelvis

        gauge = value.calibration.pose("world_model_gauge")
        root_rotation = so3_log(gauge.rotation.T @ segment_rotations[self.model.root_segment])
        joints: dict[str, np.ndarray] = {}
        rates: dict[str, np.ndarray] = {}
        for joint in self.model.joints:
            old = previous.joint_rotvec[joint.joint_id]
            rest = so3_exp(value.calibration.vector(joint.rest_rotation_slot, 3))
            candidate = so3_log(rest.T @ segment_rotations[joint.parent].T @ segment_rotations[joint.child])
            jump = candidate - old
            magnitude = float(np.linalg.norm(jump))
            if magnitude > 0.22:
                candidate = old + jump * (0.22 / magnitude)
                covariance[self.layout.joint_orientation(joint.joint_id), self.layout.joint_orientation(joint.joint_id)] += np.eye(3) * 8e-4
            joints[joint.joint_id] = candidate
            rates[joint.joint_id] = (candidate - old) / dt

        # Explicit synthetic bias random walks; no production parameter claim.
        bias_random_walk = np.zeros_like(covariance)
        for node in self.model.imu_ids:
            bias_random_walk[self.layout.gyro_bias(node), self.layout.gyro_bias(node)] += np.eye(3) * (2.0e-5**2 * dt)
            bias_random_walk[self.layout.accel_bias(node), self.layout.accel_bias(node)] += np.eye(3) * (2.0e-4**2 * dt)
            for kind, bias_slice in (("gyro", self.layout.gyro_bias(node)), ("accel", self.layout.accel_bias(node))):
                observed = self.bias_update_count[node][kind] > 0
                for axis in np.flatnonzero(observed):
                    covariance[bias_slice.start + axis, bias_slice.start + axis] *= 0.985
        process_contributions["bias_random_walk"] = bias_random_walk
        if value.options.bias_random_walk_covariance_enabled:
            covariance += bias_random_walk
        numerical_floor = np.eye(self.layout.dimension) * (1e-10 + (10 - valid) * 5e-8)
        process_contributions["numerical_and_missing_node_floor"] = numerical_floor
        if value.options.numerical_covariance_floor_enabled:
            covariance += numerical_floor
        self._last_process_contributions = process_contributions
        propagated = replace(
            previous, time_s=value.interval_end_s,
            root_translation_model_m=root_position,
            root_rotation_model_rotvec=root_rotation,
            root_velocity_model_mps=root_velocity,
            joint_rotvec=joints, joint_rate_rad_s=rates,
            gyro_bias_rad_s={node: gyro_bias[node].copy() for node in self.model.imu_ids},
            accel_bias_mps2={node: accel_bias[node].copy() for node in self.model.imu_ids},
            covariance=_psd(covariance),
        )
        propagated.validate(self.model)
        return propagated

    def _account(
        self,
        value: EstimatorInput,
        intervals: Mapping[str, PreintegratedInterval],
    ) -> list[ObservationAccounting]:
        received = {(row.tag_id, row.anchor_id): row for row in value.uwb_measurements}
        rows: list[ObservationAccounting] = []
        for scheduled in value.expected_schedule:
            if not scheduled.configured:
                status, event, evidence = ObservationStatus.NOT_SCHEDULED, None, "LINK_NOT_CONFIGURED"
            elif not scheduled.node_online:
                status, event, evidence = ObservationStatus.NODE_OFFLINE, None, "NODE_DECLARED_OFFLINE"
            elif not scheduled.clock_valid:
                status, event, evidence = ObservationStatus.CLOCK_INVALID, None, "SCHEDULE_CLOCK_INVALID"
            elif scheduled.modality == "IMU":
                interval = intervals[scheduled.node_id]
                if interval.boot_epoch is not None and interval.boot_epoch != scheduled.boot_epoch:
                    status, event, evidence = ObservationStatus.BOOT_EPOCH_INVALID, None, interval.status.value
                elif interval.valid:
                    status, event, evidence = ObservationStatus.RECEIVED_ACCEPTED, f"imu:{scheduled.node_id}", interval.status.value
                elif interval.status is PreintegrationStatus.INSUFFICIENT_SAMPLES:
                    status, event, evidence = ObservationStatus.EXPECTED_BUT_MISSING, None, interval.status.value
                else:
                    status, event, evidence = ObservationStatus.RECEIVED_REJECTED, f"imu:{scheduled.node_id}", interval.status.value
            else:
                measurement = received.get((str(scheduled.tag_id), int(scheduled.anchor_id)))
                if measurement is None:
                    status, event, evidence = ObservationStatus.EXPECTED_BUT_MISSING, None, "NO_MATCHING_RECEIVED_EVENT"
                elif measurement.boot_epoch != scheduled.boot_epoch:
                    status, event, evidence = ObservationStatus.BOOT_EPOCH_INVALID, measurement.event_uid, "BOOT_EPOCH_MISMATCH"
                elif not measurement.clock_valid:
                    status, event, evidence = ObservationStatus.CLOCK_INVALID, measurement.event_uid, "MEASUREMENT_CLOCK_INVALID"
                elif measurement.availability_time_s > scheduled.deadline_s:
                    status, event, evidence = ObservationStatus.LATE, measurement.event_uid, "ARRIVED_AFTER_DEADLINE"
                else:
                    status, event, evidence = ObservationStatus.RECEIVED_ACCEPTED, measurement.event_uid, "MATCHED_SCHEDULE"
            rows.append(ObservationAccounting(
                scheduled.schedule_uid, scheduled.modality, scheduled.persistent_id,
                status, event, evidence,
            ))
        return rows

    def _uwb_evidence(
        self,
        value: EstimatorInput,
        previous: KeyframeState,
        propagated: KeyframeState,
        accounting: list[ObservationAccounting],
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str, bool, str, bool]], bool]:
        accepted_ids = {row.event_uid for row in accounting if row.status is ObservationStatus.RECEIVED_ACCEPTED}
        residuals: list[dict[str, Any]] = []
        by_link: dict[str, bool] = {}
        by_tag: dict[str, list[bool]] = defaultdict(list)
        by_anchor: dict[str, list[bool]] = defaultdict(list)
        for measurement in value.uwb_measurements:
            if measurement.event_uid not in accepted_ids:
                continue
            state_at = interpolate_state(previous, propagated, measurement.measurement_time_s)
            tag = self.model.tag_phase_centres(state_at, value.calibration)[measurement.tag_id]
            anchor = value.calibration.vector(f"anchor_position:{measurement.anchor_id}", 3)
            delay = float(value.calibration.vector(f"anchor_delay:{measurement.anchor_id}", 1)[0])
            predicted = float(np.linalg.norm(anchor - tag) + delay)
            innovation = measurement.range_m - predicted
            normalized = innovation / measurement.sigma_m
            bad = abs(normalized) > 6.0
            link = measurement.link_id
            by_link[link] = bad
            by_tag[measurement.tag_id].append(bad)
            by_anchor[str(measurement.anchor_id)].append(bad)
            residuals.append({
                "measurement": measurement, "innovation_m": float(innovation),
                "normalized": float(normalized), "bad": bad,
            })

        missing_links = {
            row.persistent_id for row in accounting
            if row.modality == "UWB" and row.status is ObservationStatus.EXPECTED_BUT_MISSING
        }
        all_links = {
            row.persistent_id for row in accounting if row.modality == "UWB"
        }
        for link in all_links:
            bad = link in missing_links or by_link.get(link, False)
            by_link[link] = bad
            tag, anchor = link.split(":")
            if link in missing_links:
                by_tag[tag].append(True)
                by_anchor[anchor].append(True)
        evidence: list[tuple[str, str, bool, str, bool]] = []
        for link, bad in by_link.items():
            evidence.append(("uwb_link_health", link, bad, "EXPECTED_MISSING_OR_RANGE_INNOVATION", False))
        for tag in self.model.tag_ids:
            values = by_tag.get(tag, [])
            fraction = float(sum(values) / len(values)) if values else 0.0
            evidence.append(("uwb_tag_health", tag, bool(values) and fraction >= 0.45, f"BAD_LINK_FRACTION={fraction:.6f}", False))
        for anchor in range(8):
            values = by_anchor.get(str(anchor), [])
            fraction = float(sum(values) / len(values)) if values else 0.0
            evidence.append(("uwb_anchor_health", str(anchor), bool(values) and fraction >= 0.45, f"BAD_LINK_FRACTION={fraction:.6f}", False))
        total_expected = sum(row.modality == "UWB" for row in accounting)
        total_missing = sum(row.modality == "UWB" and row.status is ObservationStatus.EXPECTED_BUT_MISSING for row in accounting)
        global_bad = total_expected > 0 and total_missing == total_expected
        evidence.append(("global_observability_health", "UWB", global_bad, f"MISSING={total_missing}/{total_expected}", False))

        # Model inconsistency is inferred from structured multi-link residuals.
        ambiguous = False
        for tag in self.model.tag_ids:
            values = [row["normalized"] for row in residuals if row["measurement"].tag_id == tag]
            strong = [number for number in values if abs(number) > 2.8]
            structured = len(strong) >= 3 and (min(strong) < 0.0 < max(strong) or np.std(strong) > 1.4)
            if structured:
                ambiguous = True
            evidence.append(("model_consistency_health", tag, structured, f"STRUCTURED_RESIDUAL_COUNT={len(strong)}", False))
        return residuals, evidence, ambiguous

    def _full_uwb_update(
        self,
        value: EstimatorInput,
        previous: KeyframeState,
        propagated: KeyframeState,
        residuals: Sequence[Mapping[str, Any]],
    ) -> tuple[KeyframeState, dict[str, Any], dict[str, ObservationStatus]]:
        if not value.options.uwb_enabled or not residuals:
            covariance = propagated.covariance.copy()
            missing_accommodation = np.zeros_like(covariance)
            missing_accommodation[0:3, 0:3] = np.eye(3) * 1.0e-7
            missing_accommodation[5, 5] = 1.0e-7
            if value.options.missing_observation_covariance_enabled:
                covariance += missing_accommodation
            self.covariance_trace_rows.append({
                "step": value.step_index,
                "mechanism": "missing_observation_covariance_accommodation",
                "enabled": value.options.missing_observation_covariance_enabled,
                "root_position_trace_m2": float(np.trace(missing_accommodation[0:3, 0:3])),
                "yaw_variance_rad2": float(missing_accommodation[5, 5]),
            })
            return replace(propagated, covariance=_psd(covariance)), {
                "accepted": 0, "information_eigenvalues": [0.0, 0.0, 0.0],
                "information_eigenvectors": np.eye(3).tolist(), "weak_direction": [0.0, 0.0, 1.0],
                "full_3d_absolute_observability": False, "correction_norm_m": 0.0,
                "directional_inflation_m2": 0.0, "explicit_directional_inflation_m2": 0.0,
            }, {}

        h_rows: list[np.ndarray] = []
        innovations: list[float] = []
        variances: list[float] = []
        status: dict[str, ObservationStatus] = {}
        jacobians: dict[str, np.ndarray] = {}
        if self._calibration_signature is None:
            calibration_payload = {
                key: slot.value for key, slot in sorted(value.calibration.slots.items())
                if key.startswith(("joint_", "imu_extrinsic:", "tag_lever:", "world_model_gauge"))
            }
            self._calibration_signature = hashlib.sha256(
                json.dumps(calibration_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        root_information = np.zeros((3, 3))
        for row in residuals:
            measurement: UwbMeasurement = row["measurement"]
            bad = bool(row["bad"])
            link_health = self.health.channel("uwb_link_health", measurement.link_id)
            tag_health = self.health.channel("uwb_tag_health", measurement.tag_id)
            anchor_health = self.health.channel("uwb_anchor_health", str(measurement.anchor_id))
            health_weight = min(link_health.weight, tag_health.weight, anchor_health.weight)
            if not value.options.health_accommodation_enabled:
                health_weight = 1.0
            elif bad and link_health.state is HealthState.ISOLATED:
                status[measurement.event_uid] = ObservationStatus.RECEIVED_REJECTED
                continue
            if not value.options.recovery_ramp_enabled and any(channel.state in {
                HealthState.RECOVERING, HealthState.REQUALIFYING, HealthState.CONTROLLED_REENTRY,
            } for channel in (link_health, tag_health, anchor_health)):
                health_weight = 1.0
            robust = 1.0
            normalized = abs(float(row["normalized"]))
            if value.options.health_accommodation_enabled and value.options.robust_weighting_enabled and normalized > 2.5:
                robust = 2.5 / normalized
            weight = max(1e-6, health_weight * robust)
            state_at = interpolate_state(previous, propagated, measurement.measurement_time_s)
            if measurement.tag_id not in jacobians:
                cache_key = (self._calibration_signature, measurement.tag_id)
                if cache_key not in _POINT_JACOBIAN_CACHE:
                    _POINT_JACOBIAN_CACHE[cache_key] = self.model.point_jacobian(
                        state_at, value.calibration, "tag", measurement.tag_id,
                    )
                jacobians[measurement.tag_id] = _POINT_JACOBIAN_CACHE[cache_key]
            point = self.model.tag_phase_centres(state_at, value.calibration)[measurement.tag_id]
            anchor = value.calibration.vector(f"anchor_position:{measurement.anchor_id}", 3)
            direction = (point - anchor) / max(1e-12, np.linalg.norm(point - anchor))
            h = np.zeros(self.layout.dimension)
            configuration_row = direction @ jacobians[measurement.tag_id]
            h[0:6] = configuration_row[0:6]
            h[9:36] = configuration_row[6:33]
            variance = measurement.sigma_m**2 / weight
            h_rows.append(h)
            innovations.append(float(row["innovation_m"]))
            variances.append(float(variance))
            root_information += np.outer(h[:3], h[:3]) / variance
            status[measurement.event_uid] = ObservationStatus.RECEIVED_ACCEPTED
            self.innovation_rows.append({
                "step": value.step_index, "event_uid": measurement.event_uid,
                "innovation_m": float(row["innovation_m"]), "sigma_m": measurement.sigma_m,
                # Scalar UWB residual, hence one measurement degree of freedom.
                # `nis` is retained for schema compatibility and is explicitly
                # the raw nominal-noise scalar NIS.  Effective NIS includes the
                # health/robust information weight used by this update.
                "nis": float(row["normalized"]) ** 2,
                "raw_nominal_scalar_nis": float(row["normalized"]) ** 2,
                "effective_weighted_scalar_nis": float(row["normalized"]) ** 2 * weight,
                "measurement_degrees_of_freedom": 1,
                "health_weight": health_weight, "robust_weight": robust, "weight": weight,
            })

        if len(h_rows) < 4:
            covariance = propagated.covariance.copy()
            covariance[0:3, 0:3] += np.eye(3) * 1.0e-3
            return replace(propagated, covariance=_psd(covariance)), {
                "accepted": len(h_rows), "information_eigenvalues": [0.0, 0.0, 0.0],
                "information_eigenvectors": np.eye(3).tolist(), "weak_direction": [0.0, 0.0, 1.0],
                "full_3d_absolute_observability": False, "correction_norm_m": 0.0,
                "directional_inflation_m2": 0.0, "explicit_directional_inflation_m2": 0.0,
            }, status

        eigenvalues, eigenvectors = np.linalg.eigh(root_information)
        weak_direction = eigenvectors[:, 0]
        whitened_root_h = np.asarray(h_rows)[:, 0:3] / np.sqrt(np.asarray(variances))[:, None]
        _, _, independent_vt = np.linalg.svd(whitened_root_h, full_matrices=False)
        independent_weak_direction = independent_vt[-1]
        if float(weak_direction @ independent_weak_direction) < 0.0:
            independent_weak_direction = -independent_weak_direction
        projector_difference = float(np.linalg.norm(
            np.outer(weak_direction, weak_direction)
            - np.outer(independent_weak_direction, independent_weak_direction), ord="fro",
        ))
        ratio = float(eigenvalues[0] / max(eigenvalues[-1], 1e-12))
        weak = ratio < 0.12
        h = np.asarray(h_rows)
        residual = np.asarray(innovations)
        r = np.diag(variances)
        p = propagated.covariance
        s = h @ p @ h.T + r
        gain = np.linalg.solve(s, h @ p).T
        dx = gain @ residual
        root_norm = float(np.linalg.norm(dx[0:3]))
        if root_norm > 0.08:
            dx *= 0.08 / root_norm
        # Keep individual articulated corrections bounded while preserving FK.
        for joint in self.model.joint_ids:
            block = self.layout.joint_orientation(joint)
            magnitude = float(np.linalg.norm(dx[block]))
            if magnitude > 0.10:
                dx[block] *= 0.10 / magnitude
        identity = np.eye(self.layout.dimension)
        kh = gain @ h
        if value.options.covariance_update_form == "JOSEPH":
            covariance_r = r
            if not value.options.recovery_covariance_accommodation_enabled:
                covariance_r = np.diag([
                    float(item["measurement"].sigma_m**2) for item in residuals
                    if item["measurement"].event_uid in status
                    and status[item["measurement"].event_uid] is ObservationStatus.RECEIVED_ACCEPTED
                ])
            posterior = (identity - kh) @ p @ (identity - kh).T + gain @ covariance_r @ gain.T
        elif value.options.covariance_update_form == "SIMPLE":
            posterior = (identity - kh) @ p
        else:
            raise ValueError(f"unsupported covariance update form: {value.options.covariance_update_form}")
        posterior = 0.5 * (posterior + posterior.T)
        basis_covariance = eigenvectors.T @ posterior[0:3, 0:3] @ eigenvectors
        weak_variance = float(basis_covariance[0, 0])
        orthogonal_mean_variance = float(np.mean(np.diag(basis_covariance)[1:]))
        # This is naturally retained weak-direction variance, not an additive
        # covariance term.  Geometry is represented exactly once by H and R.
        directional_inflation = (
            max(0.0, weak_variance - orthogonal_mean_variance)
            if weak and value.options.directional_geometry_enabled else 0.0
        )
        explicit_directional_inflation = 0.0
        if weak and value.options.legacy_explicit_weak_inflation_enabled:
            explicit_directional_inflation = 1.5e-3
            projector_direction = (
                independent_weak_direction if value.options.independent_directional_projector
                else weak_direction
            )
            posterior[0:3, 0:3] += explicit_directional_inflation * np.outer(
                projector_direction, projector_direction,
            )
        updated = _apply_increment(propagated, self.layout, dx)
        updated = replace(updated, covariance=_psd(posterior))
        updated.validate(self.model)
        correction = float(np.linalg.norm(updated.root_translation_model_m - propagated.root_translation_model_m))
        self.correction_norms.append(correction)
        information = {
            "accepted": len(h_rows), "information_eigenvalues": eigenvalues.tolist(),
            "information_eigenvectors": eigenvectors.tolist(), "weak_direction": weak_direction.tolist(),
            "weak_eigenvalue_ratio": ratio, "full_3d_absolute_observability": not weak,
            "correction_norm_m": correction, "directional_inflation_m2": directional_inflation,
            "directional_inflation_definition": "natural weak variance excess after H/R update; not added to covariance",
            "explicit_directional_inflation_m2": explicit_directional_inflation,
            "independent_weak_direction": independent_weak_direction.tolist(),
            "directional_projector_frobenius_difference": projector_difference,
            "prior_weak_variance_m2": float(weak_direction @ p[0:3, 0:3] @ weak_direction),
            "post_measurement_weak_variance_m2": weak_variance,
            "final_weak_variance_m2": float(weak_direction @ posterior[0:3, 0:3] @ weak_direction),
            "measurement_weak_variance_contraction_m2": float(
                weak_direction @ p[0:3, 0:3] @ weak_direction - weak_variance
            ),
            "process_contributions_weak_variance_m2": {
                name: float(weak_direction @ contribution[0:3, 0:3] @ weak_direction)
                for name, contribution in self._last_process_contributions.items()
            },
            "full_state_jacobian_nonzero_columns": np.flatnonzero(np.max(np.abs(h), axis=0) > 1e-12).tolist(),
        }
        self.covariance_trace_rows.append({"step": value.step_index, **information})
        self.information_rows.append({"step": value.step_index, **information})
        return updated, information, status

    def _attribution_and_scope(self, ambiguous: bool, information: Mapping[str, Any]) -> tuple[str, AffectedScope]:
        bad = {HealthState.DEGRADED, HealthState.ISOLATED}
        anchors = tuple(entity for (kind, entity), channel in self.health.channels.items() if kind == "uwb_anchor_health" and channel.state in bad)
        tags = tuple(entity for (kind, entity), channel in self.health.channels.items() if kind == "uwb_tag_health" and channel.state in bad)
        imus = tuple(entity for (kind, entity), channel in self.health.channels.items() if kind == "imu_health" and channel.state in bad)
        links = tuple(entity for (kind, entity), channel in self.health.channels.items() if kind == "uwb_link_health" and channel.state in bad)
        global_state = self.health.channel("global_observability_health", "UWB").state
        combined = tuple(sorted(set(tags) & set(imus)))
        if global_state in bad or information.get("accepted", 0) == 0:
            return "GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS", AffectedScope("UWB", "global_observability", ("UWB",), "HIGH")
        if combined:
            return "BAD_IMU_AND_NODE_UWB_SCOPE", AffectedScope("COMBINED", "node", combined, "HIGH")
        if len(anchors) >= 2 or not information.get("full_3d_absolute_observability", True):
            return "GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS", AffectedScope("UWB", "anchor_geometry", anchors, "HIGH")
        if anchors:
            return "BAD_ANCHOR", AffectedScope("UWB", "anchor", anchors, "HIGH")
        if tags:
            return "BAD_TAG_OR_NODE_UWB", AffectedScope("UWB", "tag", tags, "HIGH")
        if imus:
            return "BAD_IMU_OR_TIME_STREAM", AffectedScope("IMU", "node", imus, "HIGH")
        if links:
            return "SINGLE_UWB_LINK_OR_NLOS", AffectedScope("UWB", "link", links, "MEDIUM")
        if ambiguous:
            entities = tuple(entity for (kind, entity), channel in self.health.channels.items() if kind == "model_consistency_health" and channel.state is not HealthState.HEALTHY)
            return "AMBIGUOUS_MULTI_CAUSE", AffectedScope("CROSS_MODAL", "model_or_slip", entities, "AMBIGUOUS")
        return "NO_FAULT", AffectedScope("NONE", "none", (), "HIGH")

    def _mode(self, attribution: str, information: Mapping[str, Any]) -> DegradedMode:
        channels = list(self.health.channels.values())
        recovery = [channel.state for channel in channels]
        if HealthState.RECOVERING in recovery:
            return DegradedMode.RECOVERING
        if HealthState.REQUALIFYING in recovery:
            return DegradedMode.REQUALIFYING
        if HealthState.CONTROLLED_REENTRY in recovery:
            return DegradedMode.CONTROLLED_REENTRY
        global_state = self.health.channel("global_observability_health", "UWB").state
        if global_state is not HealthState.HEALTHY and information.get("accepted", 0) == 0:
            return DegradedMode.GLOBAL_UWB_OUTAGE
        severe = {HealthState.DEGRADED, HealthState.ISOLATED}
        anchors = [channel for (kind, _), channel in self.health.channels.items() if kind == "uwb_anchor_health" and channel.state in severe]
        tags = [channel for (kind, _), channel in self.health.channels.items() if kind == "uwb_tag_health" and channel.state in severe]
        imus = [channel for (kind, _), channel in self.health.channels.items() if kind == "imu_health" and channel.state in severe]
        combined = set(channel.entity_id for channel in tags) & set(channel.entity_id for channel in imus)
        if combined:
            return DegradedMode.SINGLE_NODE_IMU_AND_UWB_DEGRADED
        if len(anchors) >= 2 or not information.get("full_3d_absolute_observability", True):
            return DegradedMode.MULTI_ANCHOR_GEOMETRY_DEGRADED
        if anchors:
            return DegradedMode.SINGLE_ANCHOR_ISOLATED
        if tags:
            return DegradedMode.SINGLE_TAG_UWB_DEGRADED
        if imus:
            return DegradedMode.SINGLE_IMU_DEGRADED
        if attribution == "AMBIGUOUS_MULTI_CAUSE":
            return DegradedMode.AMBIGUOUS_MODEL_OR_SLIP_MISMATCH
        links = [channel for (kind, _), channel in self.health.channels.items() if kind == "uwb_link_health" and channel.state in severe]
        if links:
            return DegradedMode.SINGLE_UWB_LINK_DEGRADED
        if any(channel.state is HealthState.SUSPECT for channel in channels):
            return DegradedMode.SUSPECT
        return DegradedMode.NORMAL

    def step(self, value: EstimatorInput) -> EstimatorOutput:
        """Execute one interval from measurement/schedule authority only."""
        if set(value.imu_streams) != set(self.model.imu_ids):
            raise ValueError("EstimatorInput must carry the ten-node IMU inventory")
        previous = self.state
        intervals = self.preintegrator.integrate_async(
            value.imu_streams,
            gyro_bias_by_node=previous.gyro_bias_rad_s,
            accel_bias_by_node=previous.accel_bias_mps2,
        )
        accounting = self._account(value, intervals)
        gyro, accel, imu_evidence = self._bias_observer(value, intervals)
        propagated = self._propagate(value, intervals, gyro, accel)
        residuals, uwb_evidence, ambiguous = self._uwb_evidence(value, previous, propagated, accounting)
        # All modality channels are updated together, then node health is composed once.
        self.health.update_modalities((*imu_evidence, *uwb_evidence), value.interval_end_s)
        updated, information, uwb_status = self._full_uwb_update(value, previous, propagated, residuals)
        accounting = [
            replace(row, status=uwb_status[row.event_uid])
            if row.event_uid in uwb_status else row
            for row in accounting
        ]
        attribution, scope = self._attribution_and_scope(ambiguous, information)
        mode = self._mode(attribution, information)
        transitions = self.health.transitions()
        new_transitions = transitions[self._last_transition_count:]
        self._last_transition_count = len(transitions)
        self.state = updated
        self.states.append(updated)
        self.mode_history.append(mode.value)
        self.covariance_traces.append(float(np.trace(updated.covariance)))
        self.root_covariance_traces.append(float(np.trace(updated.covariance[0:3, 0:3])))
        self.yaw_variances.append(float(updated.covariance[5, 5]))
        bias_evidence = {
            node: {
                "gyro_estimate_rad_s": updated.gyro_bias_rad_s[node].tolist(),
                "accelerometer_estimate_mps2": updated.accel_bias_mps2[node].tolist(),
                "gyro_update_count": self.bias_update_count[node]["gyro"].tolist(),
                "accelerometer_update_count": self.bias_update_count[node]["accel"].tolist(),
                "gyro_max_correction_rad_s": self.bias_max_correction[node]["gyro"].tolist(),
                "accelerometer_max_correction_mps2": self.bias_max_correction[node]["accel"].tolist(),
                "sensitivity_rank": int(np.linalg.matrix_rank(self.bias_sensitivity[node], tol=1e-10)),
            }
            for node in self.model.imu_ids
        }
        output = EstimatorOutput(
            updated, tuple(accounting), self.health.snapshot(), tuple(new_transitions),
            scope, mode, attribution,
            {
                "uwb": information,
                "preintegration_status": {node: interval.status.value for node, interval in intervals.items()},
                "normalized_innovations": [float(row["normalized"]) for row in residuals],
                "shared_fk_joint_closure_max_m": float(np.max(np.abs(self.model.kinematic_residuals(updated, value.calibration)))),
            },
            {
                "trace": float(np.trace(updated.covariance)),
                "root_position_trace_m2": float(np.trace(updated.covariance[0:3, 0:3])),
                "minimum_eigenvalue": float(np.min(np.linalg.eigvalsh(updated.covariance))),
                "symmetry_max_abs": float(np.max(np.abs(updated.covariance - updated.covariance.T))),
            },
            bias_evidence,
        )
        self.outputs.append(output)
        return output

    def digest(self) -> str:
        payload = [{
            "state": _state_vector(output.state, self.layout).tolist(),
            "covariance": output.state.covariance.tolist(),
            "health": output.health_snapshot,
            "mode": output.mode.value,
            "attribution": output.attribution,
        } for output in self.outputs]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_estimator(fusion: Path, initial_reference: KeyframeState, low_motion: bool = False) -> tuple[RepairedShadowEstimator, dict[str, Any]]:
    model = corrected_body_model(
        Path(fusion), identity_mapping=CORRECT_NODE_MAP,
        identity_provenance="SYNTHETIC_R6A2A_R2_CORRECTED_FORWARD_MAP",
    )
    initial, covariance_contract = make_initial_state(model, initial_reference, low_motion)
    return RepairedShadowEstimator(model, initial), covariance_contract
